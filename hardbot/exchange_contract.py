"""Versioned machine contract for hardbot/Excel data exchange.

The JSON file is the single semantic source for the bot, the enrichment
operator and the reset-clearance gate.  The raw SHA allow-list is deliberate:
changing a mapping without introducing and reviewing a new contract version
must stop every consumer instead of silently changing production semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class ExchangeContractError(RuntimeError):
    """The checked-in exchange contract is missing, malformed or unsupported."""


CONTRACT_PATH = Path(__file__).with_name("data_exchange_contract.json")
_SUPPORTED_RAW_SHA256 = {
    1: "6a6f06af70dff45ea9b4fde2fa2f23b2fef03fd658bcaca56ab6cc6513eb60b9",
}


def _exact_dict(value: Any, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ExchangeContractError(f"{label}: unexpected object fields")
    return value


def _string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ExchangeContractError(f"{label}: expected a nonblank trimmed string")
    return value


def _strings(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ExchangeContractError(f"{label}: expected a list")
    result = tuple(_string(item, label=f"{label}[]") for item in value)
    if len(result) != len(set(result)):
        raise ExchangeContractError(f"{label}: duplicate values")
    return result


def _load() -> tuple[dict[str, Any], bytes, str]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise ExchangeContractError(
            f"exchange contract is absent or not a regular file: {CONTRACT_PATH}"
        )
    raw = CONTRACT_PATH.read_bytes()
    raw_digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ExchangeContractError("exchange contract is not valid UTF-8 JSON") from exc
    root = _exact_dict(
        payload,
        {
            "schema",
            "version",
            "artifacts",
            "form",
            "workbook_shape",
            "provenance",
            "enrichment",
            "office_overlay",
            "imports",
        },
        label="contract",
    )
    if root["schema"] != "hardbot.data-exchange-contract.v1":
        raise ExchangeContractError("unsupported exchange contract schema")
    version = root["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ExchangeContractError("contract.version must be a positive integer")
    expected_digest = _SUPPORTED_RAW_SHA256.get(version)
    if expected_digest is None or raw_digest != expected_digest:
        raise ExchangeContractError(
            "exchange contract bytes are not approved for this version; "
            "bump/review the version and update the supported SHA"
        )

    artifacts = _exact_dict(
        root["artifacts"],
        {
            "snapshot_kind",
            "enrichment_evidence_schema",
            "enrichment_file_map_schema",
            "clearance_receipt_schema",
        },
        label="artifacts",
    )
    for key, value in artifacts.items():
        _string(value, label=f"artifacts.{key}")

    form = _exact_dict(
        root["form"],
        {"protocol_version", "dropdown_keys", "text_keys"},
        label="form",
    )
    protocol_version = form["protocol_version"]
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version < 1
    ):
        raise ExchangeContractError("form.protocol_version must be positive")
    dropdown_keys = _strings(form["dropdown_keys"], label="form.dropdown_keys")
    text_keys = _strings(form["text_keys"], label="form.text_keys")
    if set(dropdown_keys) & set(text_keys):
        raise ExchangeContractError("form dropdown/text keys overlap")

    workbook = _exact_dict(
        root["workbook_shape"],
        {
            "required_sheet_name",
            "header_search_rows",
            "header_policy",
            "type_row",
            "required_keys_policy",
            "unknown_headers_policy",
            "duplicate_headers_policy",
            "export_schema_sha256",
            "contract_key",
            "responsible_key",
            "contract_title",
            "responsible_title",
            "header_aliases",
            "allowed_ignored_headers",
            "allowed_ignored_header_patterns",
        },
        label="workbook_shape",
    )
    _string(
        workbook["required_sheet_name"],
        label="workbook_shape.required_sheet_name",
    )
    header_search_rows = workbook["header_search_rows"]
    if (
        isinstance(header_search_rows, bool)
        or not isinstance(header_search_rows, int)
        or header_search_rows < 1
        or header_search_rows > 100
    ):
        raise ExchangeContractError(
            "workbook_shape.header_search_rows must be between 1 and 100"
        )
    if workbook["header_policy"] != "hardbot-normalized-title.v1":
        raise ExchangeContractError("unsupported workbook header policy")
    type_row = _exact_dict(
        workbook["type_row"],
        {
            "offset_from_header",
            "published_master",
            "test_subset",
            "allowed_tags",
            "allowed_blank_keys",
        },
        label="workbook_shape.type_row",
    )
    if type_row["offset_from_header"] != 1:
        raise ExchangeContractError(
            "workbook type row must immediately follow the header"
        )
    if type_row["published_master"] != "required":
        raise ExchangeContractError(
            "published master type row must be required"
        )
    if type_row["test_subset"] != "optional":
        raise ExchangeContractError("test-subset type row must be optional")
    type_tags = _strings(
        type_row["allowed_tags"],
        label="workbook_shape.type_row.allowed_tags",
    )
    if any(tag != tag.casefold() for tag in type_tags):
        raise ExchangeContractError("workbook type tags must be lowercase")
    _strings(
        type_row["allowed_blank_keys"],
        label="workbook_shape.type_row.allowed_blank_keys",
    )
    required_policy = _exact_dict(
        workbook["required_keys_policy"],
        {"published_master", "test_subset"},
        label="workbook_shape.required_keys_policy",
    )
    if required_policy["published_master"] != "all-export-schema-fields":
        raise ExchangeContractError(
            "published master must require the full export schema"
        )
    subset_required_keys = _strings(
        required_policy["test_subset"],
        label="workbook_shape.required_keys_policy.test_subset",
    )
    if workbook["unknown_headers_policy"] != "block":
        raise ExchangeContractError("unknown workbook headers must be blocked")
    if workbook["duplicate_headers_policy"] != "block":
        raise ExchangeContractError("duplicate workbook headers must be blocked")
    export_schema_sha256 = _string(
        workbook["export_schema_sha256"],
        label="workbook_shape.export_schema_sha256",
    )
    if (
        len(export_schema_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in export_schema_sha256)
    ):
        raise ExchangeContractError(
            "workbook_shape.export_schema_sha256 must be lowercase SHA-256"
        )
    contract_key = _string(
        workbook["contract_key"],
        label="workbook_shape.contract_key",
    )
    responsible_key = _string(
        workbook["responsible_key"],
        label="workbook_shape.responsible_key",
    )
    _string(
        workbook["contract_title"],
        label="workbook_shape.contract_title",
    )
    _string(
        workbook["responsible_title"],
        label="workbook_shape.responsible_title",
    )
    aliases_raw = workbook["header_aliases"]
    if not isinstance(aliases_raw, dict):
        raise ExchangeContractError(
            "workbook_shape.header_aliases must be an object"
        )
    header_aliases: dict[str, tuple[str, ...]] = {}
    for key, values in aliases_raw.items():
        parsed_key = _string(key, label="workbook_shape.header_aliases key")
        header_aliases[parsed_key] = _strings(
            values,
            label=f"workbook_shape.header_aliases.{parsed_key}",
        )
    _strings(
        workbook["allowed_ignored_headers"],
        label="workbook_shape.allowed_ignored_headers",
    )
    ignored_patterns = _strings(
        workbook["allowed_ignored_header_patterns"],
        label="workbook_shape.allowed_ignored_header_patterns",
    )
    for index, pattern in enumerate(ignored_patterns):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ExchangeContractError(
                "workbook_shape.allowed_ignored_header_patterns"
                f"[{index}] is not valid regex"
            ) from exc

    provenance = _exact_dict(
        root["provenance"],
        {
            "marker",
            "completion_date_key",
            "snapshot_keys",
            "legacy_read_keys",
            "enrichment_source_keys",
        },
        label="provenance",
    )
    marker = _string(provenance["marker"], label="provenance.marker")
    completion_date_key = _string(
        provenance["completion_date_key"],
        label="provenance.completion_date_key",
    )
    snapshot_keys = _strings(
        provenance["snapshot_keys"],
        label="provenance.snapshot_keys",
    )
    legacy_read_keys = _strings(
        provenance["legacy_read_keys"],
        label="provenance.legacy_read_keys",
    )
    enrichment_source_keys = _strings(
        provenance["enrichment_source_keys"],
        label="provenance.enrichment_source_keys",
    )
    if completion_date_key not in snapshot_keys or completion_date_key not in (
        enrichment_source_keys
    ):
        raise ExchangeContractError("completion date is not exported/enriched")
    if not set(dropdown_keys).union(text_keys) <= set(snapshot_keys):
        raise ExchangeContractError("form fields are not all in provenance snapshot")
    if not set(legacy_read_keys) <= set(snapshot_keys):
        raise ExchangeContractError("legacy read keys are not in provenance snapshot")
    if set(legacy_read_keys) & set(enrichment_source_keys):
        raise ExchangeContractError("legacy-only keys must not be enriched")
    if set(enrichment_source_keys) - set(snapshot_keys):
        raise ExchangeContractError("enrichment sources are not provenance fields")

    enrichment = _exact_dict(
        root["enrichment"],
        {
            "allowed_merge_modes",
            "writes",
            "forbidden_targets",
        },
        label="enrichment",
    )
    allowed_merge_modes = _strings(
        enrichment["allowed_merge_modes"],
        label="enrichment.allowed_merge_modes",
    )
    forbidden_targets = _strings(
        enrichment["forbidden_targets"],
        label="enrichment.forbidden_targets",
    )
    writes_raw = enrichment["writes"]
    if not isinstance(writes_raw, list):
        raise ExchangeContractError("enrichment.writes must be a list")
    writes: list[dict[str, str]] = []
    for index, item in enumerate(writes_raw):
        rule = _exact_dict(
            item,
            {"source", "target", "merge"},
            label=f"enrichment.writes[{index}]",
        )
        writes.append(
            {
                key: _string(value, label=f"enrichment.writes[{index}].{key}")
                for key, value in rule.items()
            }
        )
    write_sources = tuple(item["source"] for item in writes)
    write_targets = tuple(item["target"] for item in writes)
    if write_sources != enrichment_source_keys or len(write_sources) != len(
        set(write_sources)
    ):
        raise ExchangeContractError(
            "enrichment writes must exactly follow provenance source order"
        )
    if len(write_targets) != len(set(write_targets)):
        raise ExchangeContractError("enrichment target columns must be unique")
    if any(item["merge"] not in allowed_merge_modes for item in writes):
        raise ExchangeContractError("enrichment write uses an unknown merge mode")
    if set(write_targets) & set(forbidden_targets):
        raise ExchangeContractError("enrichment writes a forbidden call field")

    office = _exact_dict(
        root["office_overlay"],
        {
            "result_source_key",
            "result_snapshot_key",
            "direct_edit_keys",
            "isolated_call_fields",
        },
        label="office_overlay",
    )
    result_source_key = _string(
        office["result_source_key"],
        label="office_overlay.result_source_key",
    )
    result_snapshot_key = _string(
        office["result_snapshot_key"],
        label="office_overlay.result_snapshot_key",
    )
    direct_edit_keys = _strings(
        office["direct_edit_keys"],
        label="office_overlay.direct_edit_keys",
    )
    isolated_call_fields = _strings(
        office["isolated_call_fields"],
        label="office_overlay.isolated_call_fields",
    )
    target_by_source = {item["source"]: item["target"] for item in writes}
    if target_by_source.get(result_snapshot_key) != result_source_key:
        raise ExchangeContractError("office result mapping disagrees with enrichment")
    if not set(direct_edit_keys) <= set(enrichment_source_keys):
        raise ExchangeContractError("office direct fields are not enrichment sources")
    if set(isolated_call_fields) != set(forbidden_targets):
        raise ExchangeContractError("isolated call fields disagree with forbidden targets")

    imports = _exact_dict(
        root["imports"],
        {
            "published_master_kind",
            "test_subset_kind",
            "published_master_source_id",
            "published_master_proof_schema",
            "test_subset_proof_schema",
            "legacy_publication_bootstrap",
            "allowed_source_kinds",
        },
        label="imports",
    )
    published_master_kind = _string(
        imports["published_master_kind"],
        label="imports.published_master_kind",
    )
    test_subset_kind = _string(
        imports["test_subset_kind"],
        label="imports.test_subset_kind",
    )
    _string(
        imports["published_master_source_id"],
        label="imports.published_master_source_id",
    )
    _string(
        imports["published_master_proof_schema"],
        label="imports.published_master_proof_schema",
    )
    _string(
        imports["test_subset_proof_schema"],
        label="imports.test_subset_proof_schema",
    )
    legacy_bootstrap = imports["legacy_publication_bootstrap"]
    if not isinstance(legacy_bootstrap, list):
        raise ExchangeContractError(
            "imports.legacy_publication_bootstrap must be a list"
        )
    legacy_runs: set[str] = set()
    for index, item in enumerate(legacy_bootstrap):
        receipt = _exact_dict(
            item,
            {
                "run_id",
                "plan_digest",
                "publish_receipt_sha256",
                "stage_receipt_sha256",
                "journal_sha256",
                "baseline_sha256",
                "master_sha256",
            },
            label=f"imports.legacy_publication_bootstrap[{index}]",
        )
        run_id = _string(
            receipt["run_id"],
            label=f"imports.legacy_publication_bootstrap[{index}].run_id",
        )
        if run_id in legacy_runs:
            raise ExchangeContractError(
                "legacy publication bootstrap contains duplicate run IDs"
            )
        legacy_runs.add(run_id)
        for key, value in receipt.items():
            if key == "run_id":
                continue
            legacy_digest = _string(
                value,
                label=(
                    f"imports.legacy_publication_bootstrap[{index}].{key}"
                ),
            )
            if (
                len(legacy_digest) != 64
                or any(ch not in "0123456789abcdef" for ch in legacy_digest)
            ):
                raise ExchangeContractError(
                    "legacy publication bootstrap hashes must be lowercase SHA-256"
                )
    allowed_source_kinds = _strings(
        imports["allowed_source_kinds"],
        label="imports.allowed_source_kinds",
    )
    if set(allowed_source_kinds) != {published_master_kind, test_subset_kind}:
        raise ExchangeContractError("allowed import kinds disagree with named kinds")

    return root, raw, raw_digest


CONTRACT, CONTRACT_BYTES, CONTRACT_SHA256 = _load()
CONTRACT_SCHEMA = CONTRACT["schema"]
CONTRACT_VERSION = CONTRACT["version"]

SNAPSHOT_KIND = CONTRACT["artifacts"]["snapshot_kind"]
ENRICHMENT_EVIDENCE_SCHEMA = CONTRACT["artifacts"][
    "enrichment_evidence_schema"
]
ENRICHMENT_FILE_MAP_SCHEMA = CONTRACT["artifacts"][
    "enrichment_file_map_schema"
]
CLEARANCE_RECEIPT_SCHEMA = CONTRACT["artifacts"]["clearance_receipt_schema"]

FORM_PROTOCOL_VERSION = CONTRACT["form"]["protocol_version"]
FORM_DROPDOWN_KEYS = tuple(CONTRACT["form"]["dropdown_keys"])
FORM_TEXT_KEYS = tuple(CONTRACT["form"]["text_keys"])

WORKBOOK_SHEET_NAME = CONTRACT["workbook_shape"]["required_sheet_name"]
WORKBOOK_HEADER_SEARCH_ROWS = CONTRACT["workbook_shape"]["header_search_rows"]
WORKBOOK_TYPE_ROW_OFFSET = CONTRACT["workbook_shape"]["type_row"][
    "offset_from_header"
]
WORKBOOK_TYPE_ROW_TAGS = tuple(
    CONTRACT["workbook_shape"]["type_row"]["allowed_tags"]
)
WORKBOOK_TYPE_ROW_ALLOWED_BLANK_KEYS = tuple(
    CONTRACT["workbook_shape"]["type_row"]["allowed_blank_keys"]
)
WORKBOOK_EXPORT_SCHEMA_SHA256 = CONTRACT["workbook_shape"][
    "export_schema_sha256"
]
WORKBOOK_CONTRACT_KEY = CONTRACT["workbook_shape"]["contract_key"]
WORKBOOK_RESPONSIBLE_KEY = CONTRACT["workbook_shape"]["responsible_key"]
WORKBOOK_CONTRACT_TITLE = CONTRACT["workbook_shape"]["contract_title"]
WORKBOOK_RESPONSIBLE_TITLE = CONTRACT["workbook_shape"]["responsible_title"]
WORKBOOK_HEADER_ALIASES = {
    key: tuple(values)
    for key, values in CONTRACT["workbook_shape"]["header_aliases"].items()
}
WORKBOOK_ALLOWED_IGNORED_HEADERS = tuple(
    CONTRACT["workbook_shape"]["allowed_ignored_headers"]
)
WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS = tuple(
    CONTRACT["workbook_shape"]["allowed_ignored_header_patterns"]
)
WORKBOOK_TEST_SUBSET_REQUIRED_KEYS = tuple(
    CONTRACT["workbook_shape"]["required_keys_policy"]["test_subset"]
)

PROVENANCE_MARKER = CONTRACT["provenance"]["marker"]
COMPLETION_DATE_KEY = CONTRACT["provenance"]["completion_date_key"]
SNAPSHOT_KEYS = tuple(CONTRACT["provenance"]["snapshot_keys"])
LEGACY_READ_KEYS = tuple(CONTRACT["provenance"]["legacy_read_keys"])
ENRICHMENT_SOURCE_KEYS = tuple(
    CONTRACT["provenance"]["enrichment_source_keys"]
)

ENRICHMENT_SHEET_NAME = WORKBOOK_SHEET_NAME
CONTRACT_KEY = WORKBOOK_CONTRACT_KEY
ENRICHMENT_WRITE_RULES = tuple(
    (item["source"], item["target"], item["merge"])
    for item in CONTRACT["enrichment"]["writes"]
)
ENRICHMENT_FORBIDDEN_TARGETS = frozenset(
    CONTRACT["enrichment"]["forbidden_targets"]
)

OFFICE_RESULT_SOURCE_KEY = CONTRACT["office_overlay"]["result_source_key"]
OFFICE_RESULT_SNAPSHOT_KEY = CONTRACT["office_overlay"][
    "result_snapshot_key"
]
OFFICE_DIRECT_EDIT_KEYS = tuple(CONTRACT["office_overlay"]["direct_edit_keys"])
OFFICE_CALL_FIELDS = frozenset(
    CONTRACT["office_overlay"]["isolated_call_fields"]
)

IMPORT_SOURCE_PUBLISHED_MASTER = CONTRACT["imports"]["published_master_kind"]
IMPORT_SOURCE_TEST_SUBSET = CONTRACT["imports"]["test_subset_kind"]
PUBLISHED_MASTER_SOURCE_ID = CONTRACT["imports"][
    "published_master_source_id"
]
IMPORT_SOURCE_KINDS = frozenset(CONTRACT["imports"]["allowed_source_kinds"])
PUBLISHED_MASTER_PROOF_SCHEMA = CONTRACT["imports"][
    "published_master_proof_schema"
]
TEST_SUBSET_PROOF_SCHEMA = CONTRACT["imports"]["test_subset_proof_schema"]
LEGACY_PUBLICATION_BOOTSTRAP = tuple(
    dict(item)
    for item in CONTRACT["imports"]["legacy_publication_bootstrap"]
)


def canonical_digest(value: Any) -> str:
    """Digest a JSON-safe boundary value using one cross-process encoding."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def publication_sequence(finished_at: str) -> int:
    """Stable microsecond sequence derived from a registry publication time."""

    try:
        parsed = datetime.fromisoformat(str(finished_at))
    except ValueError as exc:
        raise ExchangeContractError(
            "publication finished_at must be ISO datetime"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    sequence = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    if sequence < 1:
        raise ExchangeContractError(
            "publication finished_at must be after Unix epoch"
        )
    return sequence


def verify_export_schema(fields: Any) -> None:
    """Refuse a server/client field schema not pinned by this contract."""

    if not isinstance(fields, list) or not fields:
        raise ExchangeContractError("export schema must be a nonempty list")
    expected_fields = {"key", "title", "block", "source", "date", "num"}
    keys: list[str] = []
    titles: list[str] = []
    for index, item in enumerate(fields):
        row = _exact_dict(
            item,
            expected_fields,
            label=f"export_schema[{index}]",
        )
        keys.append(_string(row["key"], label=f"export_schema[{index}].key"))
        titles.append(
            _string(row["title"], label=f"export_schema[{index}].title")
        )
        _string(row["block"], label=f"export_schema[{index}].block")
        _string(row["source"], label=f"export_schema[{index}].source")
        if not isinstance(row["date"], bool) or not isinstance(row["num"], bool):
            raise ExchangeContractError(
                f"export_schema[{index}] date/num must be boolean"
            )
    if len(keys) != len(set(keys)):
        raise ExchangeContractError("export schema contains duplicate keys")
    if len(titles) != len(set(titles)):
        raise ExchangeContractError("export schema contains duplicate titles")
    if WORKBOOK_CONTRACT_KEY not in keys or WORKBOOK_RESPONSIBLE_KEY not in keys:
        raise ExchangeContractError(
            "export schema misses contract/responsible boundary keys"
        )
    title_by_key = {
        item["key"]: item["title"]
        for item in fields
    }
    if title_by_key[WORKBOOK_CONTRACT_KEY] != WORKBOOK_CONTRACT_TITLE:
        raise ExchangeContractError("contract key title differs from workbook contract")
    if title_by_key[WORKBOOK_RESPONSIBLE_KEY] != WORKBOOK_RESPONSIBLE_TITLE:
        raise ExchangeContractError(
            "responsible key title differs from workbook contract"
        )
    unknown_alias_keys = set(WORKBOOK_HEADER_ALIASES) - set(keys)
    if unknown_alias_keys:
        raise ExchangeContractError(
            "workbook aliases reference unknown export keys: "
            + ", ".join(sorted(unknown_alias_keys))
        )
    missing_subset_keys = set(WORKBOOK_TEST_SUBSET_REQUIRED_KEYS) - set(keys)
    if missing_subset_keys:
        raise ExchangeContractError(
            "test-subset required keys are absent from export schema: "
            + ", ".join(sorted(missing_subset_keys))
        )
    unknown_blank_type_keys = (
        set(WORKBOOK_TYPE_ROW_ALLOWED_BLANK_KEYS) - set(keys)
    )
    if unknown_blank_type_keys:
        raise ExchangeContractError(
            "type-row blank exceptions reference unknown export keys: "
            + ", ".join(sorted(unknown_blank_type_keys))
        )
    if canonical_digest(fields) != WORKBOOK_EXPORT_SCHEMA_SHA256:
        raise ExchangeContractError(
            "export schema differs from the approved workbook shape"
        )


def grid_digest(grid: Any) -> str:
    if not isinstance(grid, list):
        raise ExchangeContractError("workbook grid must be a list")

    def json_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="microseconds")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, (list, tuple)):
            return [json_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): json_value(item) for key, item in value.items()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    return canonical_digest(json_value(grid))


def _sha256(value: Any, *, label: str) -> str:
    parsed = _string(value, label=label)
    if len(parsed) != 64 or any(ch not in "0123456789abcdef" for ch in parsed):
        raise ExchangeContractError(f"{label} must be lowercase SHA-256")
    return parsed


def validate_contract_binding(value: Any) -> None:
    if value != contract_binding():
        raise ExchangeContractError(
            "data exchange contract binding differs from this runtime"
        )


def validate_import_source_proof(
    proof: Any,
    *,
    source_kind: str,
    grid: Any | None,
) -> dict[str, Any]:
    """Validate the external source receipt before any import parsing/write.

    This is an operational provenance barrier, not a replacement for the
    admin API credential.  It binds the exact grid to either a verified
    registry-cycle publication or an explicitly approved limited-test scope.
    """

    expected_grid_sha = grid_digest(grid) if grid is not None else None
    if source_kind == IMPORT_SOURCE_PUBLISHED_MASTER:
        payload = _exact_dict(
            proof,
            {
                "schema",
                "data_exchange_contract",
                "grid_sha256",
                "file_sha256",
                "publication",
            },
            label="published master source proof",
        )
        if payload["schema"] != PUBLISHED_MASTER_PROOF_SCHEMA:
            raise ExchangeContractError(
                "unsupported published-master source proof schema"
            )
        validate_contract_binding(payload["data_exchange_contract"])
        proof_grid_sha = _sha256(
            payload["grid_sha256"],
            label="published master grid_sha256",
        )
        if (
            expected_grid_sha is not None
            and proof_grid_sha != expected_grid_sha
        ):
            raise ExchangeContractError(
                "published-master proof is bound to another workbook grid"
            )
        file_sha = _sha256(
            payload["file_sha256"],
            label="published master file_sha256",
        )
        publication = _exact_dict(
            payload["publication"],
            {
                "run_id",
                "source_id",
                "publication_seq",
                "plan_digest",
                "finished_at",
                "publish_receipt_sha256",
                "stage_receipt_sha256",
                "journal_sha256",
                "baseline_sha256",
                "master_sha256",
            },
            label="published master publication",
        )
        _string(publication["run_id"], label="publication.run_id")
        source_id = _string(
            publication["source_id"],
            label="publication.source_id",
        )
        if source_id != PUBLISHED_MASTER_SOURCE_ID:
            raise ExchangeContractError(
                "published master source_id differs from the contract"
            )
        sequence = publication["publication_seq"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
        ):
            raise ExchangeContractError(
                "publication.publication_seq must be a positive integer"
            )
        finished_at = _string(
            publication["finished_at"],
            label="publication.finished_at",
        )
        try:
            datetime.fromisoformat(finished_at)
        except ValueError as exc:
            raise ExchangeContractError(
                "publication.finished_at must be ISO datetime"
            ) from exc
        if sequence != publication_sequence(finished_at):
            raise ExchangeContractError(
                "publication sequence disagrees with finished_at"
            )
        _sha256(publication["plan_digest"], label="publication.plan_digest")
        for key in (
            "publish_receipt_sha256",
            "stage_receipt_sha256",
            "journal_sha256",
            "baseline_sha256",
            "master_sha256",
        ):
            _sha256(publication[key], label=f"publication.{key}")
        if file_sha != publication["master_sha256"]:
            raise ExchangeContractError(
                "selected master bytes differ from the published master receipt"
            )
        return {
            "digest": canonical_digest(payload),
            "scope": None,
            "source_id": source_id,
            "sequence": sequence,
            "proof": payload,
        }

    if source_kind == IMPORT_SOURCE_TEST_SUBSET:
        payload = _exact_dict(
            proof,
            {
                "schema",
                "data_exchange_contract",
                "grid_sha256",
                "approval_id",
                "contracts",
            },
            label="test-subset source proof",
        )
        if payload["schema"] != TEST_SUBSET_PROOF_SCHEMA:
            raise ExchangeContractError(
                "unsupported test-subset source proof schema"
            )
        validate_contract_binding(payload["data_exchange_contract"])
        proof_grid_sha = _sha256(
            payload["grid_sha256"],
            label="test subset grid_sha256",
        )
        if (
            expected_grid_sha is not None
            and proof_grid_sha != expected_grid_sha
        ):
            raise ExchangeContractError(
                "test-subset proof is bound to another workbook grid"
            )
        approval_id = _string(
            payload["approval_id"],
            label="test subset approval_id",
        )
        raw_contracts = payload["contracts"]
        if not isinstance(raw_contracts, list):
            raise ExchangeContractError(
                "test subset contracts must be a list"
            )
        scope: dict[str, str] = {}
        ordered: list[str] = []
        for index, item in enumerate(raw_contracts):
            row = _exact_dict(
                item,
                {"contract_id", "responsible"},
                label=f"test subset contracts[{index}]",
            )
            contract_id = _string(
                row["contract_id"],
                label=f"test subset contracts[{index}].contract_id",
            )
            responsible = _string(
                row["responsible"],
                label=f"test subset contracts[{index}].responsible",
            )
            if contract_id in scope:
                raise ExchangeContractError(
                    "test subset proof contains duplicate contract IDs"
                )
            scope[contract_id] = responsible
            ordered.append(contract_id)
        if ordered != sorted(ordered):
            raise ExchangeContractError(
                "test subset proof contracts must be sorted by contract_id"
            )
        return {
            "digest": canonical_digest(payload),
            "scope": scope,
            "source_id": f"test-subset:{approval_id}",
            "sequence": 1,
            "proof": payload,
        }

    raise ExchangeContractError("unsupported import source kind")


def validate_import_source_proof_record(
    proof: Any,
    *,
    source_kind: str,
) -> dict[str, Any]:
    """Validate a trusted registrar envelope before storing it server-side.

    The exact grid is verified later at the HTTP boundary. Registration is
    intentionally unavailable over HTTP and therefore requires local/root
    access to the hardbot data store.
    """

    return validate_import_source_proof(
        proof,
        source_kind=source_kind,
        grid=None,
    )


def build_test_subset_source_proof(
    grid: list,
    *,
    approval_id: str,
    contracts: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the exact transport receipt for an already approved test scope."""

    payload = {
        "schema": TEST_SUBSET_PROOF_SCHEMA,
        "data_exchange_contract": contract_binding(),
        "grid_sha256": grid_digest(grid),
        "approval_id": approval_id,
        "contracts": sorted(
            (
                {
                    "contract_id": str(item["contract_id"]).strip(),
                    "responsible": str(item["responsible"]).strip(),
                }
                for item in contracts
            ),
            key=lambda item: item["contract_id"],
        ),
    }
    validate_import_source_proof(
        payload,
        source_kind=IMPORT_SOURCE_TEST_SUBSET,
        grid=grid,
    )
    return payload


def build_published_master_source_proof(
    grid: list,
    *,
    file_sha256: str,
    publication: dict[str, str],
) -> dict[str, Any]:
    """Build and self-check a proof from a locally verified publish receipt."""

    payload = {
        "schema": PUBLISHED_MASTER_PROOF_SCHEMA,
        "data_exchange_contract": contract_binding(),
        "grid_sha256": grid_digest(grid),
        "file_sha256": file_sha256,
        "publication": dict(publication),
    }
    validate_import_source_proof(
        payload,
        source_kind=IMPORT_SOURCE_PUBLISHED_MASTER,
        grid=grid,
    )
    return payload


def contract_binding() -> dict[str, Any]:
    """Return a fresh JSON-safe identity for receipts and snapshot artifacts."""

    return {
        "schema": CONTRACT_SCHEMA,
        "version": CONTRACT_VERSION,
        "sha256": CONTRACT_SHA256,
    }
