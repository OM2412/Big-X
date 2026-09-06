// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";

/// @title PolicyEngine
/// @notice On-chain spend/action policy per agent — the source of truth
///         for simulator.py's Simulator and AgentAccount's execution.
/// @dev SECURITY FIX: atomic consumeAction() replaces checkAction() + recordSpend()
///      to prevent replay attacks and race conditions.
contract PolicyEngine is AccessControl {
    bytes32 public constant POLICY_ADMIN_ROLE = keccak256("POLICY_ADMIN_ROLE");
    bytes32 public constant EXECUTOR_ROLE = keccak256("EXECUTOR_ROLE");
    bytes32 public constant HUMAN_APPROVER_ROLE = keccak256("HUMAN_APPROVER_ROLE");

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
    mapping(uint256 => uint256) public agentNonce;

    event PolicySet(uint256 indexed nftId, uint256 perTxLimit, uint256 dailyLimit, uint256 humanApprovalThreshold);
    event TargetAllowlisted(uint256 indexed nftId, address indexed target, bool allowed);
    event ActionApproved(uint256 indexed nftId, bytes32 indexed actionHash, uint256 nonce);
    event NonceAdvanced(uint256 indexed nftId, uint256 newNonce);
    event ActionConsumed(uint256 indexed nftId, uint256 nonce, address target, uint256 value);

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

    function approveAction(uint256 nftId, bytes32 actionHash) external onlyRole(HUMAN_APPROVER_ROLE) {
        humanApprovals[nftId][actionHash] = true;
        emit ActionApproved(nftId, actionHash, agentNonce[nftId]);
    }

    function currentNonce(uint256 nftId) external view returns (uint256) {
        return agentNonce[nftId];
    }

    function computeActionHash(uint256 nftId, address target, uint256 value, bytes calldata data) public view returns (bytes32) {
        return keccak256(abi.encode(nftId, agentNonce[nftId], target, value, data));
    }

    /// @dev READ-ONLY preflight check — used by simulator.py, NOT for execution.
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
            bytes32 actionHash = computeActionHash(nftId, target, value, data);
            if (!humanApprovals[nftId][actionHash]) return (false, "Requires human approval");
        }

        return (true, "");
    }

    /// @notice ATOMIC consume — validates policy, updates spending, advances nonce.
    ///         Called by AgentAccount.execute() in the SAME transaction.
    ///         If target execution fails, Ethereum reverts the ENTIRE transaction,
    ///         so nonce and spending roll back automatically.
    function consumeAction(
        uint256 nftId,
        uint256 expectedNonce,
        address target,
        uint256 value,
        bytes calldata data
    )
        external
        onlyRole(EXECUTOR_ROLE)
        returns (uint256 usedNonce)
    {
        uint256 current = agentNonce[nftId];

        // Explicit nonce check — makes replay invariant clear and auditable
        if (expectedNonce != current) {
            revert("Invalid nonce");
        }

        AgentPolicy memory policy = policies[nftId];
        if (!policy.exists) revert("No policy set for agent");
        if (!allowedTargets[nftId][target]) revert("Target not allowlisted");
        if (value > policy.perTxLimit) revert("Exceeds per-tx limit");

        uint256 today = block.timestamp / 1 days;
        uint256 spent = (lastResetDay[nftId] == today) ? spentToday[nftId] : 0;
        if (spent + value > policy.dailyLimit) revert("Exceeds daily limit");

        if (value > policy.humanApprovalThreshold) {
            bytes32 actionHash = keccak256(abi.encode(nftId, current, target, value, data));
            if (!humanApprovals[nftId][actionHash]) revert("Requires human approval");
        }

        // Update daily spending
        if (lastResetDay[nftId] != today) {
            lastResetDay[nftId] = today;
            spentToday[nftId] = 0;
        }
        spentToday[nftId] += value;

        // Advance nonce — this is what kills ALL stale approvals
        agentNonce[nftId] = current + 1;
        emit NonceAdvanced(nftId, current + 1);
        emit ActionConsumed(nftId, current, target, value);

        return current;
    }

    /// @dev Grant executor role to AgentAccount
    function grantExecutorRole(address executor) external onlyRole(POLICY_ADMIN_ROLE) {
        grantRole(EXECUTOR_ROLE, executor);
    }

    /// @dev Grant human approver role
    function grantHumanApproverRole(address approver) external onlyRole(POLICY_ADMIN_ROLE) {
        grantRole(HUMAN_APPROVER_ROLE, approver);
    }
}
