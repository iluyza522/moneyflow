import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyConfig:
    http: str | None = None
    https: str | None = None

    def to_dict(self) -> dict[str, str]:
        if not self.http and not self.https:
            return {}
        result = {}
        if self.http:
            result["http"] = self.http
        if self.https:
            result["https"] = self.https
        return result


def detect_proxy() -> ProxyConfig:
    """从环境变量检测代理配置"""
    return ProxyConfig(
        http=os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
        https=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
    )
