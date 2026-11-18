"""Build validated text lists from consistent upstream revisions."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_REPO = "hydraponique/roscomvpn-geosite"
ADDRESS_REPO = "hydraponique/roscomvpn-geoip"
DOMAIN_GROUPS: dict[str, tuple[str, ...]] = {
    "domains.list": ("private", "category-ru", "whitelist", "apple"),
    "exceptions.list": ("google-play", "github", "youtube", "telegram"),
    "excluded.list": ("category-ads",),
}
ADDRESS_GROUPS = ("private", "direct")
GITHUB_ASSET = r"^github-production-release-asset-[0-9a-zA-Z]{6}\.s3\.amazonaws\.com$"
DOTLESS = r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$"
MAX_DOWNLOAD = 10 * 1024 * 1024


def fetch(url: str) -> str:
    request = Request(url, headers={"User-Agent": "catalog-data", "Accept": "*/*"})
    with urlopen(request, timeout=45) as response:
        data: bytes = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"Input exceeds the size limit: {url}")
    return data.decode("utf-8-sig")


def revision(repo: str) -> str:
    result = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}.git", "refs/heads/master"],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    fields = result.stdout.split()
    if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
        raise ValueError(f"Invalid revision for {repo}")
    return fields[0]


def entries(text: str) -> list[str]:
    result = []
    for line in text.splitlines():
        value = line.split("#", 1)[0].strip()
        if value:
            result.append(value)
    if not result:
        raise ValueError("Empty input")
    return result


def domain_rules(text: str, category: str) -> list[str]:
    rules = []
    for entry in entries(text):
        kind, value = entry.split(":", 1) if ":" in entry else ("domain", entry)
        if kind == "regexp":
            if category == "github" and value == GITHUB_ASSET:
                rules.append("DOMAIN-WILDCARD,github-production-release-asset-??????.s3.amazonaws.com")
                continue
            if category == "private" and value == DOTLESS:
                # Exact private names and suffixes remain; this expression has no portable equivalent.
                continue
            raise ValueError(f"Unsupported expression in {category}: {value}")
        rule_type = {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN", "keyword": "DOMAIN-KEYWORD"}.get(kind)
        if rule_type is None or not value or re.search(r"[\s,#]", value):
            raise ValueError(f"Invalid domain entry in {category}: {entry}")
        value = value.encode("idna").decode("ascii").lower()
        if kind != "keyword":
            labels = value.split(".")
            if len(value) > 253 or any(
                not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label) for label in labels
            ):
                raise ValueError(f"Invalid domain name in {category}: {value}")
        rules.append(f"{rule_type},{value}")
    return rules


def address_rules(text: str) -> list[str]:
    networks: set[IPv4Network | IPv6Network] = {ip_network(value) for value in entries(text)}
    # Resolution is intentional: domain misses must still be checked against these ranges.
    return [
        f"{'IP-CIDR' if network.version == 4 else 'IP-CIDR6'},{network}"
        for network in sorted(
            networks, key=lambda network: (network.version, int(network.network_address), network.prefixlen)
        )
    ]


def render(name: str, rules: list[str], sources: list[str], date: str) -> str:
    unique = list(dict.fromkeys(rules))
    if not unique:
        raise ValueError(f"No output entries for {name}")
    header = [f"# {name}", f"# Updated: {date}", f"# Entries: {len(unique)}"]
    header.extend(f"# Source: {source}" for source in sources)
    header.append("# Attribution and data terms: NOTICE in this repository.")
    if name == "domains.list":
        header.append("# The private dotless-hostname expression is not included.")
    return "\n".join(header + unique) + "\n"


def build(download: Callable[[str], str] = fetch) -> dict[str, str]:
    repositories = (DOMAIN_REPO, ADDRESS_REPO)
    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = dict(zip(repositories, pool.map(revision, repositories), strict=True))
    urls = {
        category: f"https://raw.githubusercontent.com/{DOMAIN_REPO}/{revisions[DOMAIN_REPO]}/data/{category}"
        for categories in DOMAIN_GROUPS.values()
        for category in categories
    }
    urls.update(
        {
            f"address:{category}": f"https://raw.githubusercontent.com/{ADDRESS_REPO}/{revisions[ADDRESS_REPO]}/release/text/{category}.txt"
            for category in ADDRESS_GROUPS
        }
    )
    with ThreadPoolExecutor(max_workers=6) as pool:
        bodies = dict(zip(urls, pool.map(download, urls.values()), strict=True))
    date = datetime.now(UTC).date().isoformat()
    outputs = {}
    for name, categories in DOMAIN_GROUPS.items():
        rules = [rule for category in categories for rule in domain_rules(bodies[category], category)]
        outputs[name] = render(name, rules, [urls[category] for category in categories], date)
    rules = [rule for category in ADDRESS_GROUPS for rule in address_rules(bodies[f"address:{category}"])]
    outputs["addresses.list"] = render(
        "addresses.list", rules, [urls[f"address:{category}"] for category in ADDRESS_GROUPS], date
    )
    return outputs


def validate(outputs: dict[str, str], directory: Path) -> None:
    minimum = {"domains.list": 100, "exceptions.list": 20, "excluded.list": 1, "addresses.list": 1000}
    if set(outputs) != set(minimum):
        raise ValueError("The output set is incomplete")
    for name, content in outputs.items():
        count = len(entries(content))
        if count < minimum[name]:
            raise ValueError(f"Too few entries in {name}: {count}")
        existing = directory / name
        if existing.exists() and count < len(entries(existing.read_text())) / 2:
            raise ValueError(f"More than half the entries disappeared from {name}; review the upstream change")


def update(directory: Path, download: Callable[[str], str] = fetch) -> None:
    outputs = build(download)
    validate(outputs, directory)
    # No published file is changed until all downloads, conversions and checks pass.
    with TemporaryDirectory(prefix="catalog-", dir=directory) as temporary:
        staging = Path(temporary)
        for name, content in outputs.items():
            (staging / name).write_text(content, encoding="utf-8")
        for name in outputs:
            (staging / name).replace(directory / name)
    for name, content in outputs.items():
        print(f"{name}: {len(entries(content))} entries")


if __name__ == "__main__":
    update(ROOT)
