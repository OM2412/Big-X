// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC7857 {
    event AgentMinted(uint256 indexed tokenId, address indexed to, string metadataURI);
    event AgentTransferVerified(uint256 indexed tokenId, address indexed from, address indexed to);
}
