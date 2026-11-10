"""Scoped immutable attachments; content-addressed storage outside source tree."""

from pathlib import Path
import hashlib, os, uuid
from . import storage
from . import access, store
from .contract import CRMError, now, operation_id


def root():
    return storage.document_root()


def verify(row):
    path = root() / row["sha256"]
    if (
        not path.is_file()
        or path.stat().st_size != row["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
    ):
        raise CRMError("Содержимое документа не подтверждено.", "document_missing", 409)
    return path


def upload(principal, identifier, contract_id, filename, media_type, data):
    operation_id(identifier)
    access.require(principal, "document")
    if (
        not isinstance(filename, str)
        or not 1 <= len(filename) <= 200
        or any(c in filename for c in "\r\n/\\\x00")
    ):
        raise CRMError("Некорректное имя файла.")
    if media_type not in {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        raise CRMError("Поддерживаются PDF, изображения и DOCX.")
    if not data or len(data) > 10 * 1024 * 1024:
        raise CRMError("Документ должен быть не больше 10 МБ.", "size", 413)
    checksum = hashlib.sha256(data).hexdigest()
    at = now()
    with storage.connect() as conn:
        store.begin(conn, ["document:" + identifier, "contract:" + str(contract_id)])
        access.contracts(conn, principal, [contract_id], capability="document")
        previous = conn.execute(
            "SELECT * FROM crm_documents WHERE document_id=?", (identifier,)
        ).fetchone()
        if previous:
            if (
                previous["actor_uid"] != principal.actor_uid
                or previous["contract_uuid"] != contract_id
                or previous["sha256"] != checksum
                or previous["filename"] != filename
                or previous["media_type"] != media_type
            ):
                raise CRMError(
                    "ID документа использован другим запросом.",
                    "operation_conflict",
                    409,
                )
            verify(previous)
            return {key: previous[key] for key in ("document_id", "contract_uuid", "filename", "media_type", "sha256", "bytes", "created_at")}
        target = root() / checksum
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                raise CRMError(
                    "Повреждено хранилище документов.", "document_missing", 409
                )
        else:
            temp = root() / (checksum + "." + str(uuid.uuid4()) + ".tmp")
            with temp.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        conn.execute(
            "INSERT INTO crm_documents VALUES(?,?,?,?,?,?,?,?)",
            (
                identifier,
                contract_id,
                principal.actor_uid,
                filename,
                media_type,
                checksum,
                len(data),
                at,
            ),
        )
        store.audit(
            conn, principal, "document_upload", identifier, {"bytes": len(data)}
        )
        conn.commit()
        return dict(
            document_id=identifier,
            contract_uuid=contract_id,
            filename=filename,
            media_type=media_type,
            sha256=checksum,
            bytes=len(data),
            created_at=at,
        )


def download(principal, identifier):
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT * FROM crm_documents WHERE document_id=?", (identifier,)
        ).fetchone()
        if not row:
            raise CRMError("Документ не найден.", "not_found", 404)
        access.contracts(conn, principal, [row["contract_uuid"]])
        path = verify(row)
        store.audit(conn, principal, "document_read", identifier)
        conn.commit()
        return dict(row), path
