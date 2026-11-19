import pytest
from app.gateway.redaction import SecretRedactor, redact_text, shannon_entropy
from conftest import AWS_KEY, DB_PASSWORD, GITHUB_TOKEN


def rules(text: str) -> list[str]:
    return [finding.rule for finding in redact_text(text).findings]


def test_redacts_aws_access_key() -> None:
    result = redact_text(f'key = "{AWS_KEY}"')

    assert AWS_KEY not in result.text
    assert "[REDACTED:aws_access_key]" in result.text


def test_redacts_github_tokens() -> None:
    assert rules(f"t = {GITHUB_TOKEN}") == ["github_token"]
    assert rules("github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1l2") == ["github_token"]


def test_redacts_slack_stripe_google_and_llm_keys() -> None:
    assert rules("xoxb-" + "1234567890-abcdefghij") == ["slack_token"]
    assert rules("sk_live_" + "abcdefghijklmnopqrstuvwx") == ["stripe_key"]
    assert rules("AIza" + "A" * 35) == ["google_api_key"]
    assert rules("sk-ant-" + "api03-abcdefghijklmnopqrstu") == ["llm_api_key"]


def test_redacts_jwt() -> None:
    token = (
        "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "abcDEF123456_-xyz"
    )

    assert rules(token) == ["jwt"]


def test_redacts_private_key_block_preserving_line_count() -> None:
    text = (
        "a\n-----BEGIN "
        + "PRIVATE KEY-----\nAAAA\nBBBB\n-----END "
        + "PRIVATE KEY-----\nb"
    )

    result = redact_text(text)

    assert "AAAA" not in result.text
    assert result.text.count("\n") == text.count("\n")
    assert result.text.splitlines()[0] == "a"
    assert result.text.splitlines()[-1] == "b"
    assert result.findings[0].line == 2


def test_redacts_unterminated_private_key_block() -> None:
    result = redact_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB")

    assert "AAAA" not in result.text


def test_redacts_url_credentials_keeping_scheme_and_user() -> None:
    result = redact_text(f"postgres://admin:{DB_PASSWORD}@db:5432/app")

    assert result.text == "postgres://admin:[REDACTED:url_credentials]@db:5432/app"


def test_does_not_touch_urls_without_credentials() -> None:
    text = "https://example.com:8080/path"

    assert redact_text(text).text == text


@pytest.mark.parametrize(
    "line",
    [
        'password = "hunter2"',
        "password: 'hunter2'",
        '"api_key": "abcd1234"',
        'client_secret="abcd1234"',
        "DB_PASSWORD=hunter22",
        "export SECRET_TOKEN=abc12345",
        "  token: abc12345  # comentario",
    ],
)
def test_redacts_values_by_variable_name(line: str) -> None:
    result = redact_text(line)

    assert "[REDACTED:named_secret]" in result.text
    assert "hunter2" not in result.text
    assert "abcd1234" not in result.text
    assert "abc12345" not in result.text


@pytest.mark.parametrize(
    "line",
    [
        'token = os.environ["TOKEN"]',
        "password = get_password()",
        'password = ""',
        "password = None",
        "token = self.config.token",
        'password = "${DB_PASSWORD}"',
        "password = '<your-password>'",
        "secret_count = 3",
        "def check_password(value):",
    ],
)
def test_does_not_redact_non_secret_assignments(line: str) -> None:
    assert redact_text(line).text == line


def test_redacts_high_entropy_literals() -> None:
    value = "Zk3Jf9Qw2LxPv8TnR4mYb7HcD1sUe6Oa"

    result = redact_text(f'blob = "{value}"')

    assert value not in result.text
    assert "[REDACTED:high_entropy]" in result.text


@pytest.mark.parametrize(
    "literal",
    [
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "550e8400-e29b-41d4-a716-446655440000",
        "this_is_a_long_but_perfectly_normal_identifier_name",
        "src/components/forms/inputs/TextField",
    ],
)
def test_entropy_rule_ignores_hashes_uuids_and_identifiers(literal: str) -> None:
    text = f'value = "{literal}"'

    assert redact_text(text).text == text


def test_entropy_threshold_is_adjustable() -> None:
    text = 'value = "Zk3Jf9Qw2LxPv8TnR4mYb7HcD1sUe6Oa"'

    assert "[REDACTED" not in SecretRedactor(entropy_threshold=9.0).redact(text).text
    assert shannon_entropy("aaaa") == 0.0


def test_redaction_is_idempotent_and_preserves_line_numbers() -> None:
    text = f'x = 1\naws = "{AWS_KEY}"\ny = 2\npassword = "hunter2"\n'

    once = redact_text(text)
    twice = redact_text(once.text)

    assert twice.text == once.text
    assert not twice.findings
    assert once.text.splitlines()[0] == "x = 1"
    assert [f.line for f in once.findings] == [2, 4]


def test_findings_never_carry_the_secret_value() -> None:
    findings = redact_text(f'aws = "{AWS_KEY}"').findings

    assert AWS_KEY not in repr(findings)
