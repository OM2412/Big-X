// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";

/// @title BridgeContract
/// @notice Trust-minimized peg-in/peg-out for a wrapped BTC asset, secured by
///         M-of-N relayer consensus instead of a single owner key, with a
///         withdrawal timelock and per-tx/daily rate limits.
///
/// @dev This is a substantial step up from a single-signer skeleton, but it
///      is still NOT a drop-in production bridge. Two things remain outside
///      this contract's scope and need to exist before real value flows
///      through it:
///        1. The relayer set itself needs to be a real decentralized network
///           (independent operators, slashing for misbehavior) — this
///           contract only enforces that M distinct addresses with
///           RELAYER_ROLE agree, it can't stop you from granting that role
///           to five wallets you control yourself.
///        2. Native BTC-side custody/verification (that a BTC deposit
///           actually happened) is off-chain relayer responsibility here.
///           A stronger design uses a BTC light client or a network like
///           tBTC/Rootstock's own peg mechanism instead of relayer say-so.
///      Get an independent audit before mainnet, regardless.
contract BridgeContract is AccessControl, ReentrancyGuard, Pausable {
    bytes32 public constant RELAYER_ROLE = keccak256("RELAYER_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE"); // can pause, cannot move funds

    IERC20 public immutable wrappedBtc;

    uint256 public requiredConfirmations; // M-of-N
    uint256 public relayerCount;

    uint256 public constant PEG_OUT_TIMELOCK = 1 hours; // delay before a peg-out can be finalized
    uint256 public perTxLimit = 1 ether;     // placeholder unit — set to your asset's decimals
    uint256 public dailyLimit = 10 ether;
    uint256 public withdrawnToday;
    uint256 public lastResetDay;

    struct PegInRequest {
        address recipient;
        uint256 amount;
        uint256 confirmations;
        bool executed;
    }

    struct PegOutRequest {
        address requester;
        uint256 amount;
        string btcAddress;
        uint256 confirmations;
        uint64 requestedAt;
        bool executed;
    }

    mapping(bytes32 => PegInRequest) public pegInRequests; // keyed by btcTxHash
    mapping(bytes32 => mapping(address => bool)) public pegInConfirmedBy;

    mapping(uint256 => PegOutRequest) public pegOutRequests; // keyed by incrementing id
    mapping(uint256 => mapping(address => bool)) public pegOutConfirmedBy;
    uint256 public nextPegOutId;

    error AlreadyConfirmed();
    error AlreadyExecuted();
    error InsufficientConfirmations();
    error TimelockNotElapsed();
    error ExceedsPerTxLimit();
    error ExceedsDailyLimit();
    error InvalidThreshold();

    event PegInConfirmed(bytes32 indexed btcTxHash, address indexed relayer, uint256 confirmations);
    event PegInExecuted(bytes32 indexed btcTxHash, address indexed recipient, uint256 amount);
    event PegOutRequested(uint256 indexed id, address indexed requester, uint256 amount, string btcAddress);
    event PegOutConfirmed(uint256 indexed id, address indexed relayer, uint256 confirmations);
    event PegOutExecuted(uint256 indexed id);
    event RelayerAdded(address indexed relayer);
    event RelayerRemoved(address indexed relayer);
    event ThresholdUpdated(uint256 newThreshold);

    constructor(address wrappedBtcAddress, address admin, address[] memory initialRelayers, uint256 threshold) {
        wrappedBtc = IERC20(wrappedBtcAddress);
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(GUARDIAN_ROLE, admin);

        for (uint256 i = 0; i < initialRelayers.length; i++) {
            _grantRole(RELAYER_ROLE, initialRelayers[i]);
            emit RelayerAdded(initialRelayers[i]);
        }
        relayerCount = initialRelayers.length;

        if (threshold == 0 || threshold > relayerCount) revert InvalidThreshold();
        requiredConfirmations = threshold;
    }

    // ---------------------------------------------------------------------
    // Peg-in: native BTC deposit -> wrapped token released to recipient
    // ---------------------------------------------------------------------

    /// @notice A relayer attests it observed a confirmed BTC deposit. Once
    ///         `requiredConfirmations` distinct relayers agree, the wrapped
    ///         token is released automatically.
    function confirmPegIn(bytes32 btcTxHash, address recipient, uint256 amount)
        external
        onlyRole(RELAYER_ROLE)
        whenNotPaused
        nonReentrant
    {
        PegInRequest storage req = pegInRequests[btcTxHash];
        if (req.executed) revert AlreadyExecuted();
        if (pegInConfirmedBy[btcTxHash][msg.sender]) revert AlreadyConfirmed();

        if (req.recipient == address(0)) {
            req.recipient = recipient;
            req.amount = amount;
        }

        pegInConfirmedBy[btcTxHash][msg.sender] = true;
        req.confirmations += 1;
        emit PegInConfirmed(btcTxHash, msg.sender, req.confirmations);

        if (req.confirmations >= requiredConfirmations) {
            _executePegIn(btcTxHash, req);
        }
    }

    function _executePegIn(bytes32 btcTxHash, PegInRequest storage req) internal {
        req.executed = true;
        require(wrappedBtc.transfer(req.recipient, req.amount), "Transfer failed");
        emit PegInExecuted(btcTxHash, req.recipient, req.amount);
    }

    // ---------------------------------------------------------------------
    // Peg-out: wrapped token locked -> native BTC redemption
    // ---------------------------------------------------------------------

    /// @notice User locks wrapped BTC to request redemption to a native BTC address.
    ///         Subject to per-tx/daily limits and a timelock before relayers
    ///         can finalize it (gives guardians a window to pause on anomalies).
    function requestPegOut(uint256 amount, string calldata btcAddress)
        external
        whenNotPaused
        nonReentrant
        returns (uint256 id)
    {
        if (amount > perTxLimit) revert ExceedsPerTxLimit();
        require(wrappedBtc.transferFrom(msg.sender, address(this), amount), "Transfer failed");

        id = nextPegOutId++;
        pegOutRequests[id] = PegOutRequest({
            requester: msg.sender,
            amount: amount,
            btcAddress: btcAddress,
            confirmations: 0,
            requestedAt: uint64(block.timestamp),
            executed: false
        });

        emit PegOutRequested(id, msg.sender, amount, btcAddress);
    }

    /// @notice Relayer confirms the native BTC side is ready to be sent.
    ///         Requires M-of-N confirmations AND the timelock to have elapsed.
    function confirmPegOut(uint256 id) external onlyRole(RELAYER_ROLE) whenNotPaused nonReentrant {
        PegOutRequest storage req = pegOutRequests[id];
        if (req.executed) revert AlreadyExecuted();
        if (pegOutConfirmedBy[id][msg.sender]) revert AlreadyConfirmed();
        if (block.timestamp < req.requestedAt + PEG_OUT_TIMELOCK) revert TimelockNotElapsed();

        pegOutConfirmedBy[id][msg.sender] = true;
        req.confirmations += 1;
        emit PegOutConfirmed(id, msg.sender, req.confirmations);

        if (req.confirmations >= requiredConfirmations) {
            _executePegOut(id, req);
        }
    }

    function _executePegOut(uint256 id, PegOutRequest storage req) internal {
        _enforceDailyLimit(req.amount);
        req.executed = true;
        emit PegOutExecuted(id);
        // Native BTC send happens off-chain by relayers after this call.
        // Wrapped tokens stay locked in this contract (already transferred in
        // on request) — burn them here instead if your token model requires it.
    }

    function _enforceDailyLimit(uint256 amount) internal {
        uint256 today = block.timestamp / 1 days;
        if (today != lastResetDay) {
            lastResetDay = today;
            withdrawnToday = 0;
        }
        if (withdrawnToday + amount > dailyLimit) revert ExceedsDailyLimit();
        withdrawnToday += amount;
    }

    // ---------------------------------------------------------------------
    // Admin / relayer set management
    // ---------------------------------------------------------------------

    function addRelayer(address relayer) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _grantRole(RELAYER_ROLE, relayer);
        relayerCount += 1;
        emit RelayerAdded(relayer);
    }

    function removeRelayer(address relayer) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _revokeRole(RELAYER_ROLE, relayer);
        relayerCount -= 1;
        if (requiredConfirmations > relayerCount) revert InvalidThreshold();
        emit RelayerRemoved(relayer);
    }

    function setThreshold(uint256 newThreshold) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newThreshold == 0 || newThreshold > relayerCount) revert InvalidThreshold();
        requiredConfirmations = newThreshold;
        emit ThresholdUpdated(newThreshold);
    }

    function setLimits(uint256 newPerTxLimit, uint256 newDailyLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        perTxLimit = newPerTxLimit;
        dailyLimit = newDailyLimit;
    }

    /// @notice Guardians can pause instantly on anomaly detection — cannot
    ///         move funds themselves, only halt further peg-in/peg-out flow.
    function pause() external onlyRole(GUARDIAN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }
}