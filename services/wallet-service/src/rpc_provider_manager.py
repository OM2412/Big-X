
class ProviderConfig:
    def __init__(self, name, rpc_url, priority=0, chain_id=8453):
        self.name = name
        self.rpc_url = rpc_url
        self.priority = priority
        self.chain_id = chain_id


class RpcProviderManager:
    def __init__(self, chain_id, providers=None):
        self.chain_id = chain_id
        self.providers = providers or []

    def get_provider(self):
        return None

    def get_status(self):
        return {"status": "ok"}

    def start_health_checks(self):
        pass

    def stop_health_checks(self):
        pass
