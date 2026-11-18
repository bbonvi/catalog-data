"""Regression checks for feed conversion and publication safety."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.update import address_rules, domain_rules, update, validate


class ConversionTests(unittest.TestCase):
    def test_domain_formats_and_comments(self) -> None:
        self.assertEqual(
            domain_rules(
                "# comment\nexample.ru\ndomain:example.net # note\nfull:api.example.org\nkeyword:example\n", "sample"
            ),
            [
                "DOMAIN-SUFFIX,example.ru",
                "DOMAIN-SUFFIX,example.net",
                "DOMAIN,api.example.org",
                "DOMAIN-KEYWORD,example",
            ],
        )

    def test_international_domain(self) -> None:
        self.assertEqual(domain_rules("domain:пример.рф", "sample"), ["DOMAIN-SUFFIX,xn--e1afmkfd.xn--p1ai"])

    def test_ipv6_has_its_own_rule_type_and_domains_can_resolve(self) -> None:
        self.assertEqual(
            address_rules("192.0.2.0/24\n2001:db8::/32\n192.0.2.0/24"),
            ["IP-CIDR,192.0.2.0/24", "IP-CIDR6,2001:db8::/32"],
        )

    def test_invalid_inputs_stop_the_build(self) -> None:
        for text in ("", "<html>error</html>", "include:other", "domain:a,b", "domain:example.org,DIRECT", "regexp:.*"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                domain_rules(text, "sample")
        with self.assertRaises(ValueError):
            address_rules("192.0.2.1/24")

    def test_known_expressions_do_not_become_url_rules(self) -> None:
        self.assertEqual(
            domain_rules(r"regexp:^github-production-release-asset-[0-9a-zA-Z]{6}\.s3\.amazonaws\.com$", "github"),
            ["DOMAIN-WILDCARD,github-production-release-asset-??????.s3.amazonaws.com"],
        )
        self.assertEqual(
            domain_rules(r"regexp:^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$" + "\nlocal", "private"),
            ["DOMAIN-SUFFIX,local"],
        )

    @patch("scripts.update.revision", return_value="a" * 40)
    def test_missing_input_preserves_published_files(self, _revision: object) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            existing = directory / "domains.list"
            existing.write_text("last valid data\n")
            with self.assertRaises(OSError):
                update(directory, download=lambda url: (_ for _ in ()).throw(OSError("download failed")))
            self.assertEqual(existing.read_text(), "last valid data\n")
            self.assertEqual(list(directory.iterdir()), [existing])

    def test_invalid_batch_is_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            existing = directory / "domains.list"
            existing.write_text("last valid data\n")
            with patch("scripts.update.build", return_value={"domains.list": "DOMAIN,example.org\n"}):
                with self.assertRaises(ValueError):
                    update(directory)
            self.assertEqual(existing.read_text(), "last valid data\n")

    def test_large_truncation_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "domains.list").write_text("DOMAIN,old.example\n" * 1000)
            outputs = {
                "domains.list": "DOMAIN,a.example\n" * 100,
                "exceptions.list": "DOMAIN,b.example\n" * 20,
                "excluded.list": "DOMAIN,c.example\n",
                "addresses.list": "IP-CIDR,192.0.2.0/24\n" * 1000,
            }
            with self.assertRaisesRegex(ValueError, "half"):
                validate(outputs, directory)


if __name__ == "__main__":
    unittest.main()
