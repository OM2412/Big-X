import { describe, it, beforeEach } from "node:test";
import { expect } from "chai";
import hre from "hardhat";

const { ethers } = hre;

describe("AgentAccount - Session Key Security", function () {
    let owner, sessionKey, secondSessionKey, unauthorized, newOwner;
    let allowedTarget, disallowedTarget;
    let account, nft, registry, policy;
    let ownerPk, ownerSigner;

    const TOKEN_ID = 1;
    const INITIAL_BALANCE = ethers.parseEther("10");
    const ONE_ETHER = ethers.parseEther("1");
    const ZERO = 0;

    beforeEach(async function () {
        ownerPk = "0x1234567890123456789012345678901234567890123456789012345678901234";
        owner = new ethers.Wallet(ownerPk, ethers.provider);
        sessionKey = ethers.Wallet.createRandom().connect(ethers.provider);
        secondSessionKey = ethers.Wallet.createRandom().connect(ethers.provider);
        unauthorized = ethers.Wallet.createRandom().connect(ethers.provider);
        newOwner = ethers.Wallet.createRandom().connect(ethers.provider);
        allowedTarget = ethers.Wallet.createRandom().address;
        disallowedTarget = ethers.Wallet.createRandom().address;

        await owner.sendTransaction({
            to: sessionKey.address,
            value: ethers.parseEther("1")
        });
        await owner.sendTransaction({
            to: secondSessionKey.address,
            value: ethers.parseEther("1")
        });
        await owner.sendTransaction({
            to: newOwner.address,
            value: ethers.parseEther("1")
        });

        const MockERC721 = await ethers.getContractFactory("MockERC721");
        nft = await MockERC721.deploy();
        await nft.waitForDeployment();
        await nft.mint(owner.address, TOKEN_ID);

        const AgentAccount = await ethers.getContractFactory("AgentAccount");
        const implementation = await AgentAccount.deploy(owner.address);
        await implementation.waitForDeployment();

        const ERC6551RegistryMock = await ethers.getContractFactory("ERC6551RegistryMock");
        registry = await ERC6551RegistryMock.deploy(await implementation.getAddress());
        await registry.waitForDeployment();

        const accountAddr = await registry.createAccount.staticCall(
            0,
            await nft.getAddress(),
            TOKEN_ID,
            "0x"
        );
        await registry.createAccount(
            0,
            await nft.getAddress(),
            TOKEN_ID,
            "0x"
        );
        account = await ethers.getContractAt("AgentAccount", accountAddr);

        await owner.sendTransaction({
            to: accountAddr,
            value: INITIAL_BALANCE
        });

        const PolicyEngine = await ethers.getContractFactory("PolicyEngine");
        policy = await PolicyEngine.deploy();
        await policy.waitForDeployment();

        await ethers.provider.send("evm_setNextBlockTimestamp", [Math.floor(Date.now() / 1000)]);
        await ethers.provider.send("evm_mine", []);
    });

    function getTimestamp() {
        return Math.floor(Date.now() / 1000);
    }

    function getExpiry(seconds = 86400) {
        return getTimestamp() + seconds;
    }

    async function authorizeDefaultSession(limit = ONE_ETHER) {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            limit,
            targets,
            false
        );
    }

    async function signAuthorization(key, expiry, limit, targets, anyTarget, nonce, accountAddr = null) {
        const addr = accountAddr || await account.getAddress();
        const hash = ethers.solidityPackedKeccak256(
            ["address", "uint64", "uint256", "address[]", "bool", "uint256", "address"],
            [key, expiry, limit, targets, anyTarget, nonce, addr]
        );
        const signature = await owner.signMessage(ethers.getBytes(hash));
        return signature;
    }

    it("allows owner to execute any call", async function () {
        const before = await ethers.provider.getBalance(allowedTarget);
        await account.connect(owner).execute(
            allowedTarget,
            ONE_ETHER,
            "0x",
            ZERO
        );
        const after = await ethers.provider.getBalance(allowedTarget);
        expect(after - before).to.equal(ONE_ETHER);
    });

    it("reverts when non-owner executes", async function () {
        await expect(
            account.connect(unauthorized).execute(
                allowedTarget,
                ONE_ETHER,
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("allows authorized session key to execute within limit", async function () {
        await authorizeDefaultSession();
        const before = await ethers.provider.getBalance(allowedTarget);
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.3"),
            "0x",
            ZERO
        );
        const after = await ethers.provider.getBalance(allowedTarget);
        expect(after - before).to.equal(ethers.parseEther("0.3"));
    });

    it("reverts when session key exceeds spending limit", async function () {
        await authorizeDefaultSession(ethers.parseEther("0.5"));
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ONE_ETHER,
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("allows session key to use exact limit", async function () {
        await authorizeDefaultSession(ONE_ETHER);
        await account.connect(sessionKey).execute(
            allowedTarget,
            ONE_ETHER,
            "0x",
            ZERO
        );
        expect(await ethers.provider.getBalance(allowedTarget)).to.equal(ONE_ETHER);
    });

    it("enforces cumulative session spending limit", async function () {
        await authorizeDefaultSession(ethers.parseEther("0.5"));
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.3"),
            "0x",
            0
        );
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.2"),
            "0x",
            1
        );
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                2
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("reverts when target is not allowed", async function () {
        await authorizeDefaultSession();
        await expect(
            account.connect(sessionKey).execute(
                disallowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });
});
