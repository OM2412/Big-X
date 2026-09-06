// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IAgentVerifier {
    function verifyTransfer(uint256 tokenId, address from, address to, bytes calldata proof) external view returns (bool);
}
