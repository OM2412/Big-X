// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC6551Registry {
    function createAccount(address implementation, bytes32 salt, uint256 chainId, address tokenContract, uint256 tokenId) external returns (address);
}
