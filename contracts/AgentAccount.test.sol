const { expect } = require("chai");
const { ethers } = require("hardhat");

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
        await owner.sendTransaction({        cd "C:\NFT Market\agentic-defi-platform\contracts"
        npx hardhat test test/end_to_end_testing/AgentAccount.test.cjs
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

    it("allows anyTarget to bypass allowlist", async function () {
        const targets = [];
        const expiry = getExpiry();
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            true
        );
        const before = await ethers.provider.getBalance(disallowedTarget);
        await account.connect(sessionKey).execute(
            disallowedTarget,
            ethers.parseEther("0.1"),
            "0x",
            0
        );
        const after = await ethers.provider.getBalance(disallowedTarget);
        expect(after - before).to.equal(ethers.parseEther("0.1"));
    });

    it("reverts when session key expired", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry(3600);
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false
        );
        await ethers.provider.send("evm_increaseTime", [7200]);
        await ethers.provider.send("evm_mine", []);
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("reverts exactly at expiry boundary", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry(3600);
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false
        );
        await ethers.provider.send("evm_increaseTime", [3600]);
        await ethers.provider.send("evm_mine", []);
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("reverts when session key revoked", async function () {
        await authorizeDefaultSession();
        await account.connect(owner).revokeSessionKey(sessionKey.address);
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("allows re-authorization after revocation", async function () {
        await authorizeDefaultSession();
        await account.connect(owner).revokeSessionKey(sessionKey.address);
        await authorizeDefaultSession();
        const before = await ethers.provider.getBalance(allowedTarget);
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.1"),
            "0x",
            0
        );
        const after = await ethers.provider.getBalance(allowedTarget);
        expect(after - before).to.equal(ethers.parseEther("0.1"));
    });

    it("reverts when non-owner authorizes", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        await expect(
            account.connect(unauthorized).authorizeSessionKey(
                sessionKey.address,
                expiry,
                ONE_ETHER,
                targets,
                false
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("replaces session key with new permissions", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false
        );
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            ethers.parseEther("0.1"),
            targets,
            false
        );
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.5"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("reverts when unauthorized session key used", async function () {
        await authorizeDefaultSession();
        await expect(
            account.connect(secondSessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("maintains isolation between multiple session keys", async function () {
        const targetsA = [allowedTarget];
        const targetsB = [disallowedTarget];
        const expiry = getExpiry();
        await account.connect(owner).authorizeSessionKey(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targetsA,
            false
        );
        await account.connect(owner).authorizeSessionKey(
            secondSessionKey.address,
            expiry,
            ethers.parseEther("2"),
            targetsB,
            false
        );
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.5"),
            "0x",
            0
        );
        await account.connect(secondSessionKey).execute(
            disallowedTarget,
            ethers.parseEther("0.5"),
            "0x",
            0
        );
        await expect(
            account.connect(sessionKey).execute(
                disallowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                1
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("allows authorization via signature", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        const signature = await signAuthorization(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false,
            0
        );
        await account.connect(owner).authorizeSessionKeyWithSignature(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false,
            0,
            signature
        );
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.1"),
            "0x",
            0
        );
        expect(await ethers.provider.getBalance(allowedTarget)).to.equal(ethers.parseEther("0.1"));
    });

    it("reverts with invalid signature", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        const hash = ethers.solidityPackedKeccak256(
            ["address", "uint64", "uint256", "address[]", "bool", "uint256", "address"],
            [secondSessionKey.address, expiry, ONE_ETHER, targets, false, 0, await account.getAddress()]
        );
        const signature = await owner.signMessage(ethers.getBytes(hash));
        await expect(
            account.connect(owner).authorizeSessionKeyWithSignature(
                sessionKey.address,
                expiry,
                ONE_ETHER,
                targets,
                false,
                0,
                signature
            )
        ).to.be.reverted;
    });

    it("reverts with empty signature", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        await expect(
            account.connect(owner).authorizeSessionKeyWithSignature(
                sessionKey.address,
                expiry,
                ONE_ETHER,
                targets,
                false,
                0,
                "0x"
            )
        ).to.be.reverted;
    });

    it("reverts when signature fields tampered", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        const signature = await signAuthorization(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false,
            0
        );
        const tampered = signature.slice(0, -4) + "0000";
        await expect(
            account.connect(owner).authorizeSessionKeyWithSignature(
                sessionKey.address,
                expiry,
                ONE_ETHER,
                targets,
                false,
                0,
                tampered
            )
        ).to.be.reverted;
    });

    it("reverts when signature used for different account", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        const AccountFactory = await ethers.getContractFactory("AgentAccount");
        const impl2 = await AccountFactory.deploy(owner.address);
        await impl2.waitForDeployment();
        const registry2 = await ERC6551RegistryMock.deploy(await impl2.getAddress());
        await registry2.waitForDeployment();
        const addr2 = await registry2.createAccount.staticCall(0, await nft.getAddress(), TOKEN_ID + 1, "0x");
        await registry2.createAccount(0, await nft.getAddress(), TOKEN_ID + 1, "0x");
        const account2 = await ethers.getContractAt("AgentAccount", addr2);
        const signature = await signAuthorization(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false,
            0,
            await account.getAddress()
        );
        await expect(
            account2.connect(owner).authorizeSessionKeyWithSignature(
                sessionKey.address,
                expiry,
                ONE_ETHER,
                targets,
                false,
                0,
                signature
            )
        ).to.be.reverted;
    });

    it("reverts when reusing nonce", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        const signature = await signAuthorization(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false,
            0
        );
        await account.connect(owner).authorizeSessionKeyWithSignature(
            sessionKey.address,
            expiry,
            ONE_ETHER,
            targets,
            false,
            0,
            signature
        );
        await account.connect(sessionKey).execute(
            allowedTarget,
            ethers.parseEther("0.1"),
            "0x",
            0
        );
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                0
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("reverts with zero spending limit", async function () {
        await authorizeDefaultSession(0);
        await expect(
            account.connect(sessionKey).execute(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                ZERO
            )
        ).to.be.revertedWithCustomError(account, "NotAuthorized");
    });

    it("allows zero value transaction within limit", async function () {
        await authorizeDefaultSession(ONE_ETHER);
        await account.connect(sessionKey).execute(
            allowedTarget,
            0,
            "0x",
            0
        );
        expect(await ethers.provider.getBalance(allowedTarget)).to.equal(0);
    });

    it("policy rejects transaction", async function () {
        await authorizeDefaultSession();
        const realPolicy = await ethers.getContractFactory("PolicyEngine");
        const policyInstance = await realPolicy.deploy();
        await policyInstance.waitForDeployment();
        await policyInstance.setPolicy(allowedTarget, ethers.parseEther("0.1"), false);
        await expect(
            account.connect(sessionKey).executeWithPolicy(
                allowedTarget,
                ONE_ETHER,
                "0x",
                0,
                await policyInstance.getAddress()
            )
        ).to.be.revertedWith("Policy denied");
    });

    it("policy allows valid transaction", async function () {
        await authorizeDefaultSession();
        const realPolicy = await ethers.getContractFactory("PolicyEngine");
        const policyInstance = await realPolicy.deploy();
        await policyInstance.waitForDeployment();
        await policyInstance.setPolicy(allowedTarget, ONE_ETHER, true);
        await account.connect(sessionKey).executeWithPolicy(
            allowedTarget,
            ethers.parseEther("0.1"),
            "0x",
            0,
            await policyInstance.getAddress()
        );
        expect(await ethers.provider.getBalance(allowedTarget)).to.equal(ethers.parseEther("0.1"));
    });

    it("emits SessionKeyAuthorized event", async function () {
        const targets = [allowedTarget];
        const expiry = getExpiry();
        await expect(
            account.connect(owner).authorizeSessionKey(
                sessionKey.address,
                expiry,
                ONE_ETHER,
                targets,
                false
            )
        ).to.emit(account, "SessionKeyAuthorized")
            .withArgs(sessionKey.address, expiry, ONE_ETHER);
    });

    it("emits SessionKeyRevoked event", async function () {
        await authorizeDefaultSession();
        await expect(
            account.connect(owner).revokeSessionKey(sessionKey.address)
        ).to.emit(account, "SessionKeyRevoked")
            .withArgs(sessionKey.address);
    });

    it("fuzz: session key cannot exceed limit", async function () {
        const cases = [
            ["0.01", "0.001"],
            ["0.1", "0.05"],
            ["0.5", "0.5"],
            ["1", "1.1"],
            ["2", "3"],
            ["5", "4"]
        ];
        for (const [limitStr, requestedStr] of cases) {
            const limit = ethers.parseEther(limitStr);
            const requested = ethers.parseEther(requestedStr);
            const targets = [allowedTarget];
            const expiry = getExpiry();
            await account.connect(owner).authorizeSessionKey(
                sessionKey.address,
                expiry,
                limit,
                targets,
                false
            );
            if (requested > limit) {
                await expect(
                    account.connect(sessionKey).execute(
                        allowedTarget,
                        requested,
                        "0x",
                        0
                    )
                ).to.be.revertedWithCustomError(account, "NotAuthorized");
            } else {
                const before = await ethers.provider.getBalance(allowedTarget);
                await account.connect(sessionKey).execute(
                    allowedTarget,
                    requested,
                    "0x",
                    0
                );
                const after = await ethers.provider.getBalance(allowedTarget);
                expect(after - before).to.equal(requested);
            }
        }
    });

    it("ERC-6551 local/mock integration test: creates ERC-6551 style account", async function () {
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
        expect(await registry.isAccount(accountAddr)).to.be.true;
        expect(accountAddr).to.not.equal(ethers.ZeroAddress);
    });

    it("owner remains authorized after session revocation", async function () {
        await authorizeDefaultSession();
        await account.connect(owner).revokeSessionKey(sessionKey.address);
        const before = await ethers.provider.getBalance(allowedTarget);
        await account.connect(owner).execute(
            allowedTarget,
            ethers.parseEther("0.5"),
            "0x",
            0
        );
        const after = await ethers.provider.getBalance(allowedTarget);
        expect(after - before).to.equal(ethers.parseEther("0.5"));
    });

    describe("ERC-6551 Ownership Chain", function () {
        it("binds account authority to current NFT owner", async function () {
            expect(await account.owner()).to.equal(owner.address);
        });

        it("transfers account authority when NFT ownership changes", async function () {
            expect(await account.owner()).to.equal(owner.address);
            await nft.connect(owner).transferFrom(owner.address, newOwner.address, TOKEN_ID);
            expect(await account.owner()).to.equal(newOwner.address);
        });

        it("reverts when old owner executes after NFT transfer", async function () {
            await nft.connect(owner).transferFrom(owner.address, newOwner.address, TOKEN_ID);
            await expect(
                account.connect(owner).execute(
                    allowedTarget,
                    ONE_ETHER,
                    "0x",
                    ZERO
                )
            ).to.be.revertedWith("Not token owner");
        });

        it("allows new owner to execute after NFT transfer", async function () {
            await nft.connect(owner).transferFrom(owner.address, newOwner.address, TOKEN_ID);
            const before = await ethers.provider.getBalance(allowedTarget);
            await account.connect(newOwner).execute(
                allowedTarget,
                ONE_ETHER,
                "0x",
                ZERO
            );
            const after = await ethers.provider.getBalance(allowedTarget);
            expect(after - before).to.equal(ONE_ETHER);
        });
    });

    describe("ERC-6551 Account Determinism", function () {
        it("produces deterministic account address for identical parameters", async function () {
            const predicted1 = await registry.createAccount.staticCall(
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
            const predicted2 = await registry.account(
                0,
                await nft.getAddress(),
                TOKEN_ID,
                "0x"
            );
            expect(predicted1).to.equal(predicted2);
        });
    });

    describe("ERC-6551 Account Implementation Validation", function () {
        it("deployed account contains contract code", async function () {
            const code = await ethers.provider.getCode(await account.getAddress());
            expect(code).to.not.equal("0x");
        });

        it("exposes correct ERC-6551 context after deployment", async function () {
            const [chainId, tokenContract, tokenId] = await account.token();
            expect(chainId).to.equal((await ethers.provider.getNetwork()).chainId);
            expect(tokenContract).to.equal(await nft.getAddress());
            expect(tokenId).to.equal(TOKEN_ID);
        });
    });

    describe("AgentOS End-to-End Flow", function () {
        it("completes full flow: TBA -> fund -> authorize -> execute -> policy", async function () {
            await authorizeDefaultSession();
            const realPolicy = await ethers.getContractFactory("PolicyEngine");
            const policyInstance = await realPolicy.deploy();
            await policyInstance.waitForDeployment();
            await policyInstance.setPolicy(allowedTarget, ethers.parseEther("0.1"), true);
            await account.connect(sessionKey).executeWithPolicy(
                allowedTarget,
                ethers.parseEther("0.1"),
                "0x",
                0,
                await policyInstance.getAddress()
            );
            expect(await ethers.provider.getBalance(allowedTarget)).to.equal(ethers.parseEther("0.1"));
        });
    });

    describe("ERC-6551 Real Registry Fork Test", function () {
        const FORK_RPC_URL = process.env.FORK_RPC_URL;
        const FORK_BLOCK_NUMBER = process.env.FORK_BLOCK_NUMBER;

        before(function () {
            if (!FORK_RPC_URL) {
                this.skip("FORK_RPC_URL not set, skipping real registry fork test");
            }
        });

        it("interacts with actual deployed ERC-6551 registry on forked network", async function () {
            const FORKED_NETWORK = "forked";
            await ethers.provider.send("hardhat_setForkRPCUrl", [FORK_RPC_URL]);
            if (FORK_BLOCK_NUMBER) {
                await ethers.provider.send("hardhat_setForkBlockNumber", [FORK_BLOCK_NUMBER]);
            }

            const MockERC721 = await ethers.getContractFactory("MockERC721");
            const nftFork = await MockERC721.deploy();
            await nftFork.waitForDeployment();
            const tokenId = 999999;
            await nftFork.mint(owner.address, tokenId);

            const AgentAccount = await ethers.getContractFactory("AgentAccount");
            const implementation = await AgentAccount.deploy(owner.address);
            await implementation.waitForDeployment();

            const realRegistryAddress = "0xaFF050F97E2A3f85a9Bc1f3f9E3F7D1A8bC9E2F3";
            const registryInterface = new ethers.Interface([
                "function createAccount(address implementation, bytes32 salt, uint256 chainId, address tokenContract, uint256 tokenId) external returns (address)",
                "function account(address implementation, bytes32 salt, uint256 chainId, address tokenContract, uint256 tokenId) external view returns (address)",
                "function isAccount(address account) external view returns (bool)"
            ]);
            const realRegistry = new ethers.Contract(realRegistryAddress, registryInterface, owner);

            const salt = ethers.hexlify(ethers.randomBytes(32));
            const accountAddr = await realRegistry.createAccount.staticCall(
                await implementation.getAddress(),
                salt,
                (await ethers.provider.getNetwork()).chainId,
                await nftFork.getAddress(),
                tokenId
            );
            await realRegistry.createAccount(
                await implementation.getAddress(),
                salt,
                (await ethers.provider.getNetwork()).chainId,
                await nftFork.getAddress(),
                tokenId
            );
            expect(await realRegistry.isAccount(accountAddr)).to.be.true;

            const accountFork = await ethers.getContractAt("AgentAccount", accountAddr);
            expect(await accountFork.owner()).to.equal(owner.address);
        });
    });
});
