// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";
import "@openzeppelin/contracts/interfaces/IERC1271.sol";
import "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import "@openzeppelin/contracts/utils/cryptography/SignatureChecker.sol";
import "./interfaces/IERC7857.sol";
import "./interfaces/IERC6551Registry.sol";
import "./interfaces/IERC6551Account.sol";

// ============================================================================
// AGENT ACCOUNT — ERC-6551 Token Bound Account
// ============================================================================

/// @title AgentAccount
/// @notice Minimal ERC-6551 Token Bound Account. Deployed once per agent NFT
///         via the registry's `createAccount`; whoever owns the NFT controls
///         this account and everything it holds. Transferring the NFT
///         transfers the wallet.
contract AgentAccount is IERC6551Account, IERC1271 {
    uint256 public state;

    receive() external payable {}

    function token() public view returns (uint256 chainId, address tokenContract, uint256 tokenId) {
        bytes memory footer = new bytes(0x60);
        assembly {
            extcodecopy(address(), add(footer, 0x20), 0x4d, 0x60)
        }
        return abi.decode(footer, (uint256, address, uint256));
    }

    function owner() public view returns (address) {
        (uint256 chainId, address tokenContract, uint256 tokenId) = token();
        if (chainId != block.chainid) return address(0);
        return IERC721(tokenContract).ownerOf(tokenId);
    }

    function isValidSigner(address signer, bytes calldata) external view returns (bytes4) {
        if (signer == owner()) return IERC6551Account.isValidSigner.selector;
        return bytes4(0);
    }

    function isValidSignature(bytes32 hash, bytes memory signature) external view returns (bytes4) {
        bool isValid = SignatureChecker.isValidSignatureNow(owner(), hash, signature);
        if (isValid) return IERC1271.isValidSignature.selector;
        return bytes4(0);
    }

    function execute(address to, uint256 value, bytes calldata data, uint8 operation)
        external payable returns (bytes memory result)
    {
        require(msg.sender == owner(), "Not token owner");
        require(operation == 0, "Only plain calls supported");

        state++;
        bool success;
        (success, result) = to.call{value: value}(data);
        require(success, "Call failed");
    }
}

// ============================================================================
// CAPABILITY REGISTRY
// ============================================================================

/// @title CapabilityRegistry
/// @notice Defines what each capability bit means and what risk tier it
///         carries. AgentRegistry stores which bits an agent HAS; this
///         contract defines what each bit MEANS.
contract CapabilityRegistry is AccessControl {
    bytes32 public constant CAPABILITY_ADMIN_ROLE = keccak256("CAPABILITY_ADMIN_ROLE");

    struct CapabilityDef {
        string name;
        string description;
        uint8 riskTier; // 0 = read-only, 1 = standard write, 2 = high-risk (bridging, leverage)
        bool exists;
    }

    uint256 public constant CAP_SWAP = 1 << 0;
    uint256 public constant CAP_BRIDGE = 1 << 1;
    uint256 public constant CAP_YIELD_FARM = 1 << 2;
    uint256 public constant CAP_NFT_TRADE = 1 << 3;
    uint256 public constant CAP_LENDING = 1 << 4;
    uint256 public constant CAP_PRICE_FEED_READ = 1 << 5;

    mapping(uint256 => CapabilityDef) public capabilities;

    event CapabilityDefined(uint256 indexed bit, string name, uint8 riskTier);

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(CAPABILITY_ADMIN_ROLE, admin);

        _define(0, "swap", "Execute DEX swaps", 1);
        _define(1, "bridge", "Move assets cross-chain", 2);
        _define(2, "yield_farm", "Deposit/withdraw into yield protocols", 1);
        _define(3, "nft_trade", "Buy/sell NFTs", 1);
        _define(4, "lending", "Borrow/lend/leverage positions", 2);
        _define(5, "price_feed_read", "Read-only market data access", 0);
    }

    function defineCapability(uint256 bitPosition, string calldata name, string calldata description, uint8 riskTier)
        external onlyRole(CAPABILITY_ADMIN_ROLE)
    {
        _define(bitPosition, name, description, riskTier);
    }

    function _define(uint256 bitPosition, string memory name, string memory description, uint8 riskTier) internal {
        uint256 bit = 1 << bitPosition;
        capabilities[bit] = CapabilityDef(name, description, riskTier, true);
        emit CapabilityDefined(bit, name, riskTier);
    }

    function isDefined(uint256 capabilityBit) external view returns (bool) {
        return capabilities[capabilityBit].exists;
    }

    function riskTierOf(uint256 capabilityBit) external view returns (uint8) {
        return capabilities[capabilityBit].riskTier;
    }

    function highestRiskTier(uint256 capabilityMask) external view returns (uint8 highest) {
        for (uint256 bit = 1; bit <= capabilityMask; bit <<= 1) {
            if (capabilityMask & bit != 0 && capabilities[bit].exists) {
                if (capabilities[bit].riskTier > highest) highest = capabilities[bit].riskTier;
            }
        }
    }
}

// ============================================================================
// POLICY ENGINE
// ============================================================================

/// @title PolicyEngine
/// @notice On-chain spend/action policy per agent, enforced independently of
///         your backend so a compromised service key can't silently bypass it.
contract PolicyEngine is AccessControl {
    bytes32 public constant POLICY_ADMIN_ROLE = keccak256("POLICY_ADMIN_ROLE");

    struct AgentPolicy {
        uint256 perTxLimit;
        uint256 dailyLimit;
        uint256 humanApprovalThreshold;
        bool exists;
    }

    mapping(uint256 => AgentPolicy) public policies;
    mapping(uint256 => mapping(address => bool)) public allowedTargets;
    mapping(uint256 => uint256) public spentToday;
    mapping(uint256 => uint256) public lastResetDay;
    mapping(uint256 => mapping(bytes32 => bool)) public humanApprovals;

    event PolicySet(uint256 indexed nftId, uint256 perTxLimit, uint256 dailyLimit, uint256 humanApprovalThreshold);
    event TargetAllowlisted(uint256 indexed nftId, address indexed target, bool allowed);
    event ActionApproved(uint256 indexed nftId, bytes32 indexed actionHash);

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(POLICY_ADMIN_ROLE, admin);
    }

    function setPolicy(uint256 nftId, uint256 perTxLimit, uint256 dailyLimit, uint256 humanApprovalThreshold)
        external onlyRole(POLICY_ADMIN_ROLE)
    {
        policies[nftId] = AgentPolicy(perTxLimit, dailyLimit, humanApprovalThreshold, true);
        emit PolicySet(nftId, perTxLimit, dailyLimit, humanApprovalThreshold);
    }

    function setTargetAllowed(uint256 nftId, address target, bool allowed) external onlyRole(POLICY_ADMIN_ROLE) {
        allowedTargets[nftId][target] = allowed;
        emit TargetAllowlisted(nftId, target, allowed);
    }

    function approveAction(uint256 nftId, bytes32 actionHash) external onlyRole(POLICY_ADMIN_ROLE) {
        humanApprovals[nftId][actionHash] = true;
        emit ActionApproved(nftId, actionHash);
    }

    function checkAction(uint256 nftId, address target, uint256 value, bytes calldata data)
        external view returns (bool allowed, string memory reason)
    {
        AgentPolicy memory policy = policies[nftId];
        if (!policy.exists) return (false, "No policy set for agent");
        if (!allowedTargets[nftId][target]) return (false, "Target not allowlisted");
        if (value > policy.perTxLimit) return (false, "Exceeds per-tx limit");

        uint256 today = block.timestamp / 1 days;
        uint256 spent = (lastResetDay[nftId] == today) ? spentToday[nftId] : 0;
        if (spent + value > policy.dailyLimit) return (false, "Exceeds daily limit");

        if (value > policy.humanApprovalThreshold) {
            bytes32 actionHash = keccak256(abi.encode(nftId, target, value, data));
            if (!humanApprovals[nftId][actionHash]) return (false, "Requires human approval");
        }

        return (true, "");
    }

    function recordSpend(uint256 nftId, uint256 value) external onlyRole(POLICY_ADMIN_ROLE) {
        uint256 today = block.timestamp / 1 days;
        if (lastResetDay[nftId] != today) {
            lastResetDay[nftId] = today;
            spentToday[nftId] = 0;
        }
        spentToday[nftId] += value;
    }
}

// ============================================================================
// AGENT REGISTRY — core hub
// ============================================================================

/// @title AgentRegistry
/// @notice Core hub tying together identity, lifecycle, capability grants,
///         ERC-6551 account provisioning, TEE/ZKP attestation, and pointers
///         to PolicyEngine (above) and RevenueTracker/Marketplace (in
///         ERC7857IntelligentNFT.sol).
contract AgentRegistry is AccessControl, Pausable {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");

    enum LifecycleState {
        Created,
        Provisioning,
        Active,
        Suspended,
        Deprecated,
        Archived
    }

    enum VerifierType {
        None,
        TEE,
        ZKP
    }

    struct Attestation {
        VerifierType verifierType;
        address verifierContract;
        bytes32 lastAttestationHash;
        uint64 lastAttestedAt;
    }

    struct AgentIdentity {
        string name;
        string persona;
        address creator;
        uint32 version;
        uint64 createdAt;
    }

    struct AgentRecord {
        address owner;
        uint256 capabilities;
        string modelVersion;
        string metadataURI;
        string endpoint;
        address tokenBoundAccount;
        LifecycleState state;
    }

    IERC7857 public immutable agentNFT;
    IERC6551Registry public immutable tbaRegistry;
    address public immutable tbaImplementation;
    CapabilityRegistry public capabilityRegistry;

    // Modular pointers — set post-deploy, live in ERC7857IntelligentNFT.sol
    address public policyEngine;
    address public revenueTracker;
    address public marketplace;

    mapping(uint256 => AgentIdentity) private _identities;
    mapping(uint256 => AgentRecord) private _records;
    mapping(uint256 => Attestation) private _attestations;
    mapping(uint256 => bool) private _isRegistered;
    uint256[] private _allNftIds;

    error AlreadyRegistered();
    error NotRegistered();
    error NotAgentOwner();
    error InvalidLifecycleTransition();
    error CapabilityNotDefined();
    error CapabilityRequiresElevatedRole(uint8 riskTier);

    event AgentRegistered(uint256 indexed nftId, address indexed creator, address indexed owner, string name);
    event LifecycleChanged(uint256 indexed nftId, LifecycleState from, LifecycleState to);
    event TokenBoundAccountCreated(uint256 indexed nftId, address account);
    event CapabilityGranted(uint256 indexed nftId, uint256 capability);
    event CapabilityRevoked(uint256 indexed nftId, uint256 capability);
    event AttestationRecorded(uint256 indexed nftId, VerifierType verifierType, bytes32 attestationHash);
    event OwnerSynced(uint256 indexed nftId, address newOwner);
    event EndpointUpdated(uint256 indexed nftId, string endpoint);
    event ModulesUpdated(address policyEngine, address revenueTracker, address marketplace);

    modifier onlyRegistered(uint256 nftId) {
        if (!_isRegistered[nftId]) revert NotRegistered();
        _;
    }

    constructor(
        address agentNFTAddress,
        address tbaRegistryAddress,
        address tbaImplementationAddress,
        address capabilityRegistryAddress,
        address admin
    ) {
        agentNFT = IERC7857(agentNFTAddress);
        tbaRegistry = IERC6551Registry(tbaRegistryAddress);
        tbaImplementation = tbaImplementationAddress;
        capabilityRegistry = CapabilityRegistry(capabilityRegistryAddress);

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(REGISTRAR_ROLE, admin);
    }

    function registerAgent(
        uint256 nftId,
        address owner,
        string calldata name,
        string calldata persona,
        uint256 capabilities,
        string calldata modelVersion,
        string calldata metadataURI,
        string calldata endpoint
    ) external onlyRole(REGISTRAR_ROLE) whenNotPaused {
        if (_isRegistered[nftId]) revert AlreadyRegistered();
        _validateCapabilities(capabilities);

        _identities[nftId] = AgentIdentity({
            name: name,
            persona: persona,
            creator: owner,
            version: 1,
            createdAt: uint64(block.timestamp)
        });

        _records[nftId] = AgentRecord({
            owner: owner,
            capabilities: capabilities,
            modelVersion: modelVersion,
            metadataURI: metadataURI,
            endpoint: endpoint,
            tokenBoundAccount: address(0),
            state: LifecycleState.Created
        });

        _isRegistered[nftId] = true;
        _allNftIds.push(nftId);
        emit AgentRegistered(nftId, owner, owner, name);
    }

    function provisionAccount(uint256 nftId, bytes32 salt) external onlyRegistered(nftId) whenNotPaused {
        AgentRecord storage record = _records[nftId];
        if (record.state != LifecycleState.Created) revert InvalidLifecycleTransition();

        address tbaAddress = tbaRegistry.createAccount(tbaImplementation, salt, block.chainid, address(agentNFT), nftId);

        record.tokenBoundAccount = tbaAddress;
        _transitionState(nftId, LifecycleState.Provisioning);
        emit TokenBoundAccountCreated(nftId, tbaAddress);
    }

    function activate(uint256 nftId) external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId) {
        AgentRecord storage record = _records[nftId];
        if (record.state != LifecycleState.Provisioning) revert InvalidLifecycleTransition();
        _transitionState(nftId, LifecycleState.Active);
    }

    function suspend(uint256 nftId) external onlyRegistered(nftId) {
        AgentRecord storage record = _records[nftId];
        bool isRegistrar = hasRole(REGISTRAR_ROLE, msg.sender);
        bool isOwner = record.owner == msg.sender;
        require(isRegistrar || isOwner, "Not authorized");
        if (record.state != LifecycleState.Active) revert InvalidLifecycleTransition();
        _transitionState(nftId, LifecycleState.Suspended);
    }

    function reactivate(uint256 nftId) external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId) {
        AgentRecord storage record = _records[nftId];
        if (record.state != LifecycleState.Suspended) revert InvalidLifecycleTransition();
        _transitionState(nftId, LifecycleState.Active);
    }

    function deprecate(uint256 nftId) external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId) {
        AgentRecord storage record = _records[nftId];
        if (record.state != LifecycleState.Active && record.state != LifecycleState.Suspended) {
            revert InvalidLifecycleTransition();
        }
        _transitionState(nftId, LifecycleState.Deprecated);
    }

    function archive(uint256 nftId) external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId) {
        AgentRecord storage record = _records[nftId];
        if (record.state != LifecycleState.Deprecated) revert InvalidLifecycleTransition();
        _transitionState(nftId, LifecycleState.Archived);
    }

    function _transitionState(uint256 nftId, LifecycleState newState) internal {
        LifecycleState old = _records[nftId].state;
        _records[nftId].state = newState;
        emit LifecycleChanged(nftId, old, newState);
    }

    function grantCapability(uint256 nftId, uint256 capability) external onlyRegistered(nftId) {
        if (!capabilityRegistry.isDefined(capability)) revert CapabilityNotDefined();

        uint8 riskTier = capabilityRegistry.riskTierOf(capability);
        if (riskTier >= 2) {
            if (!hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) revert CapabilityRequiresElevatedRole(riskTier);
        } else {
            if (!hasRole(REGISTRAR_ROLE, msg.sender)) revert CapabilityRequiresElevatedRole(riskTier);
        }

        _records[nftId].capabilities |= capability;
        emit CapabilityGranted(nftId, capability);
    }

    function revokeCapability(uint256 nftId, uint256 capability) external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId) {
        _records[nftId].capabilities &= ~capability;
        emit CapabilityRevoked(nftId, capability);
    }

    function hasCapability(uint256 nftId, uint256 capability) external view returns (bool) {
        return _records[nftId].capabilities & capability != 0;
    }

    function _validateCapabilities(uint256 capabilities) internal view {
        for (uint256 bit = 1; bit <= capabilities; bit <<= 1) {
            if (capabilities & bit != 0 && !capabilityRegistry.isDefined(bit)) revert CapabilityNotDefined();
        }
    }

    function recordAttestation(uint256 nftId, VerifierType verifierType, address verifierContract, bytes32 attestationHash)
        external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId)
    {
        _attestations[nftId] = Attestation({
            verifierType: verifierType,
            verifierContract: verifierContract,
            lastAttestationHash: attestationHash,
            lastAttestedAt: uint64(block.timestamp)
        });
        emit AttestationRecorded(nftId, verifierType, attestationHash);
    }

    function getAttestation(uint256 nftId) external view returns (Attestation memory) {
        return _attestations[nftId];
    }

    function syncOwner(uint256 nftId, address newOwner) external onlyRole(REGISTRAR_ROLE) onlyRegistered(nftId) {
        _records[nftId].owner = newOwner;
        emit OwnerSynced(nftId, newOwner);
    }

    function updateEndpoint(uint256 nftId, string calldata endpoint) external onlyRegistered(nftId) {
        AgentRecord storage record = _records[nftId];
        if (record.owner != msg.sender) revert NotAgentOwner();
        record.endpoint = endpoint;
        emit EndpointUpdated(nftId, endpoint);
    }

    function setModules(address policyEngineAddress, address revenueTrackerAddress, address marketplaceAddress)
        external onlyRole(DEFAULT_ADMIN_ROLE)
    {
        policyEngine = policyEngineAddress;
        revenueTracker = revenueTrackerAddress;
        marketplace = marketplaceAddress;
        emit ModulesUpdated(policyEngineAddress, revenueTrackerAddress, marketplaceAddress);
    }

    function getIdentity(uint256 nftId) external view returns (AgentIdentity memory) {
        return _identities[nftId];
    }

    function getAgent(uint256 nftId) external view returns (AgentRecord memory) {
        return _records[nftId];
    }

    function totalAgents() external view returns (uint256) {
        return _allNftIds.length;
    }

    function getAgentsPage(uint256 offset, uint256 limit) external view returns (uint256[] memory nftIds) {
        uint256 total = _allNftIds.length;
        if (offset >= total) return new uint256[](0);
        uint256 end = offset + limit > total ? total : offset + limit;

        nftIds = new uint256[](end - offset);
        for (uint256 i = offset; i < end; i++) {
            nftIds[i - offset] = _allNftIds[i];
        }
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }
}