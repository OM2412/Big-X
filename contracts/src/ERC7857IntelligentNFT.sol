// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC721/extensions/ERC721Enumerable.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "./interfaces/IERC7857.sol";
import "./interfaces/IAgentVerifier.sol";

struct AgentMetadata {
    bytes32 encryptedDataHash;
    string metadataURI;
    address verifier;
}

// ============================================================================
// ERC7857 INTELLIGENT NFT
// ============================================================================

/// @title ERC7857IntelligentNFT
/// @notice Ownable, tradeable AI-agent NFT. Model weights/memory/character are
///         encrypted off-chain (IPFS/Arweave); this contract stores a hash +
///         pointer, enforces verified transfers via a pluggable TEE/ZK
///         verifier, and pays resale royalties (ERC-2981, per-token).
contract ERC7857IntelligentNFT is ERC721Enumerable, ERC2981, AccessControl, Pausable, IERC7857 {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    uint256 private _nextTokenId;
    mapping(uint256 => AgentMetadata) private _agentMetadata;

    error NotAgentOwner();
    error VerifierRejectedTransfer();
    error InvalidRoyaltyReceiver();

    constructor(address admin, address defaultRoyaltyReceiver, uint96 defaultRoyaltyBps)
        ERC721("Intelligent Agent", "AGENT")
    {
        if (defaultRoyaltyReceiver == address(0)) revert InvalidRoyaltyReceiver();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MINTER_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _setDefaultRoyalty(defaultRoyaltyReceiver, defaultRoyaltyBps);
    }

    function mintAgent(address to, string calldata metadataURI, bytes32 encryptedDataHash)
        external onlyRole(MINTER_ROLE) whenNotPaused returns (uint256 tokenId)
    {
        tokenId = _nextTokenId++;
        _safeMint(to, tokenId);

        _agentMetadata[tokenId] = AgentMetadata({
            encryptedDataHash: encryptedDataHash,
            metadataURI: metadataURI,
            verifier: address(0)
        });

        emit AgentMinted(tokenId, to, metadataURI);
    }

    function batchMintAgents(
        address[] calldata recipients,
        string[] calldata metadataURIs,
        bytes32[] calldata encryptedDataHashes
    ) external onlyRole(MINTER_ROLE) whenNotPaused {
        require(
            recipients.length == metadataURIs.length && recipients.length == encryptedDataHashes.length,
            "Length mismatch"
        );
        for (uint256 i = 0; i < recipients.length; i++) {
            uint256 tokenId = _nextTokenId++;
            _safeMint(recipients[i], tokenId);
            _agentMetadata[tokenId] = AgentMetadata({
                encryptedDataHash: encryptedDataHashes[i],
                metadataURI: metadataURIs[i],
                verifier: address(0)
            });
            emit AgentMinted(tokenId, recipients[i], metadataURIs[i]);
        }
    }

    function getAgentMetadata(uint256 tokenId) external view returns (AgentMetadata memory) {
        _requireOwned(tokenId);
        return _agentMetadata[tokenId];
    }

    function setVerifier(uint256 tokenId, address verifier) external {
        if (ownerOf(tokenId) != msg.sender) revert NotAgentOwner();
        _agentMetadata[tokenId].verifier = verifier;
    }

    function setTokenRoyalty(uint256 tokenId, address receiver, uint96 feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setTokenRoyalty(tokenId, receiver, feeBps);
    }

    function transferWithProof(address from, address to, uint256 tokenId, bytes calldata proof)
        external whenNotPaused
    {
        if (ownerOf(tokenId) != from) revert NotAgentOwner();
        address verifier = _agentMetadata[tokenId].verifier;

        if (verifier != address(0)) {
            bool valid = IAgentVerifier(verifier).verifyTransfer(tokenId, from, to, proof);
            if (!valid) revert VerifierRejectedTransfer();
        }

        _safeTransfer(from, to, tokenId, "");
        emit AgentTransferVerified(tokenId, from, to);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function _update(address to, uint256 tokenId, address auth)
        internal override(ERC721Enumerable) whenNotPaused returns (address)
    {
        return super._update(to, tokenId, auth);
    }

    function _increaseBalance(address account, uint128 value) internal override(ERC721Enumerable) {
        super._increaseBalance(account, value);
    }

    function supportsInterface(bytes4 interfaceId)
        public view override(ERC721Enumerable, ERC2981, AccessControl) returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}

// ============================================================================
// REVENUE TRACKER
// ============================================================================

/// @title RevenueTracker
/// @notice Tracks earnings per agent and handles withdrawal, including
///         lineage royalties to a parent agent if this one was forked.
contract RevenueTracker is AccessControl, ReentrancyGuard {
    bytes32 public constant TREASURY_ROLE = keccak256("TREASURY_ROLE");

    struct LineageInfo {
        uint256 parentNftId;
        uint96 royaltyBps;
        bool hasParent;
    }

    mapping(uint256 => uint256) public totalEarned;
    mapping(uint256 => uint256) public withdrawable;
    mapping(uint256 => LineageInfo) public lineage;
    mapping(uint256 => address) public agentOwnerCache;

    event RevenueRecorded(uint256 indexed nftId, uint256 amount, string source);
    event LineageSet(uint256 indexed nftId, uint256 indexed parentNftId, uint96 royaltyBps);
    event Withdrawn(uint256 indexed nftId, address indexed to, uint256 amount);

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(TREASURY_ROLE, admin);
    }

    function syncOwner(uint256 nftId, address owner) external onlyRole(TREASURY_ROLE) {
        agentOwnerCache[nftId] = owner;
    }

    function setLineage(uint256 nftId, uint256 parentNftId, uint96 royaltyBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(royaltyBps <= 5000, "Royalty too high");
        lineage[nftId] = LineageInfo(parentNftId, royaltyBps, true);
        emit LineageSet(nftId, parentNftId, royaltyBps);
    }

    function recordRevenue(uint256 nftId, string calldata source) external payable onlyRole(TREASURY_ROLE) {
        require(msg.value > 0, "No value sent");

        uint256 amount = msg.value;
        totalEarned[nftId] += amount;

        LineageInfo memory info = lineage[nftId];
        if (info.hasParent && info.royaltyBps > 0) {
            uint256 royalty = (amount * info.royaltyBps) / 10_000;
            withdrawable[info.parentNftId] += royalty;
            withdrawable[nftId] += amount - royalty;
        } else {
            withdrawable[nftId] += amount;
        }

        emit RevenueRecorded(nftId, amount, source);
    }

    function withdraw(uint256 nftId) external nonReentrant {
        address owner = agentOwnerCache[nftId];
        require(owner == msg.sender, "Not agent owner");

        uint256 amount = withdrawable[nftId];
        require(amount > 0, "Nothing to withdraw");

        withdrawable[nftId] = 0;
        (bool success, ) = owner.call{value: amount}("");
        require(success, "Withdraw failed");

        emit Withdrawn(nftId, owner, amount);
    }
}

// ============================================================================
// MARKETPLACE
// ============================================================================

/// @title Marketplace
/// @notice Listing/escrow state for buying and selling agent NFTs, with
///         ERC-2981 royalty payout and a protocol fee.
contract Marketplace is AccessControl, ReentrancyGuard {
    bytes32 public constant FEE_ADMIN_ROLE = keccak256("FEE_ADMIN_ROLE");

    IERC721 public immutable agentNFT;
    uint96 public protocolFeeBps = 250; // 2.5%
    address public feeRecipient;

    struct Listing {
        address seller;
        uint256 price;
        bool active;
    }

    mapping(uint256 => Listing) public listings;

    error NotTokenOwner();
    error NotApprovedForMarketplace();
    error ListingNotActive();
    error InsufficientPayment();

    event Listed(uint256 indexed nftId, address indexed seller, uint256 price);
    event ListingCancelled(uint256 indexed nftId);
    event Sold(uint256 indexed nftId, address indexed seller, address indexed buyer, uint256 price);
    event ProtocolFeeUpdated(uint96 bps);

    constructor(address agentNFTAddress, address admin, address feeRecipientAddress) {
        agentNFT = IERC721(agentNFTAddress);
        feeRecipient = feeRecipientAddress;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(FEE_ADMIN_ROLE, admin);
    }

    function list(uint256 nftId, uint256 price) external {
        if (agentNFT.ownerOf(nftId) != msg.sender) revert NotTokenOwner();
        if (agentNFT.getApproved(nftId) != address(this) && !agentNFT.isApprovedForAll(msg.sender, address(this))) {
            revert NotApprovedForMarketplace();
        }

        listings[nftId] = Listing(msg.sender, price, true);
        emit Listed(nftId, msg.sender, price);
    }

    function cancelListing(uint256 nftId) external {
        Listing storage listing = listings[nftId];
        require(listing.seller == msg.sender, "Not seller");
        listing.active = false;
        emit ListingCancelled(nftId);
    }

    function buy(uint256 nftId) external payable nonReentrant {
        Listing storage listing = listings[nftId];
        if (!listing.active) revert ListingNotActive();
        if (msg.value < listing.price) revert InsufficientPayment();

        address seller = listing.seller;
        uint256 price = listing.price;
        listing.active = false;

        uint256 royaltyAmount;
        address royaltyReceiver;
        if (_supportsRoyalty()) {
            (royaltyReceiver, royaltyAmount) = IERC2981(address(agentNFT)).royaltyInfo(nftId, price);
        }

        uint256 protocolFee = (price * protocolFeeBps) / 10_000;
        uint256 sellerProceeds = price - protocolFee - royaltyAmount;

        agentNFT.safeTransferFrom(seller, msg.sender, nftId);

        if (royaltyAmount > 0 && royaltyReceiver != address(0)) {
            (bool royaltyOk, ) = royaltyReceiver.call{value: royaltyAmount}("");
            require(royaltyOk, "Royalty payment failed");
        }
        (bool feeOk, ) = feeRecipient.call{value: protocolFee}("");
        require(feeOk, "Fee payment failed");
        (bool sellerOk, ) = seller.call{value: sellerProceeds}("");
        require(sellerOk, "Seller payment failed");

        if (msg.value > price) {
            (bool refundOk, ) = msg.sender.call{value: msg.value - price}("");
            require(refundOk, "Refund failed");
        }

        emit Sold(nftId, seller, msg.sender, price);
    }

    function _supportsRoyalty() internal view returns (bool) {
        try IERC2981(address(agentNFT)).supportsInterface(type(IERC2981).interfaceId) returns (bool ok) {
            return ok;
        } catch {
            return false;
        }
    }

    function setProtocolFee(uint96 bps) external onlyRole(FEE_ADMIN_ROLE) {
        require(bps <= 1000, "Fee too high");
        protocolFeeBps = bps;
        emit ProtocolFeeUpdated(bps);
    }
}
