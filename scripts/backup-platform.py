"""Encrypted application backup plus isolated SQLite restore-and-hash verification.

Does not claim to replace PostgreSQL physical backups or the provider's PITR.
Run with backend environment variables. Output and key must be kept separately.
"""
import argparse
from datetime import datetime,date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED,ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from sqlalchemy import MetaData,Table,select,inspect,DateTime,Date,Numeric
from services.api import models,feature_models
from services.api import inquiries as inquiry_models  # noqa: F401  (table registration)
from services.api.billing import models as billing_models
from services.api.service_orders import models as service_order_models
from services.api.operations import models as operation_models
from services.api.metrics import models as metric_models
from services.api.retention import models as retention_models
from services.api.font_assets import models as font_models
from services.api.config import Settings
from services.api.database import Base,build_database
from services.api.storage import build_storage,LocalStorage


def encode(value):
    if isinstance(value,(datetime,date)): return {"__datetime__":value.isoformat()}
    if isinstance(value,Decimal): return {"__decimal__":str(value)}
    raise TypeError(type(value).__name__)


def decode_rows(rows,table):
    # JSON columns may legitimately contain keys that resemble our tagged
    # scalar values. Decode only typed SQL columns, never arbitrary JSON.
    for row in rows:
        for name,value in row.items():
            column=table.columns[name]
            if isinstance(column.type,(DateTime,Date)) and isinstance(value,dict) and set(value)=={"__datetime__"}:
                row[name]=datetime.fromisoformat(value["__datetime__"]) if isinstance(column.type,DateTime) else date.fromisoformat(value["__datetime__"])
            elif isinstance(column.type,Numeric) and isinstance(value,dict) and set(value)=={"__decimal__"}:
                row[name]=Decimal(value["__decimal__"])
    return rows


def backup(output,key_path):
    settings=Settings();engine,sessions=build_database(settings);storage=build_storage(settings)
    output=output.resolve()
    if key_path.resolve().is_relative_to(output): raise ValueError("Keep the encryption key outside the backup directory")
    output.mkdir(parents=True,exist_ok=False)
    key=key_path.read_bytes() if key_path.exists() else Fernet.generate_key()
    if not key_path.exists(): key_path.write_bytes(key)
    from services.api.retention.storage_lifecycle import begin_backup,add_backup_pins,finish_backup
    with engine.connect() as probe:
        can_pin="storage_backup_runs" in inspect(probe).get_table_names()
    if (settings.storage_gc_delete_enabled or settings.retention_customer_delete_enabled) and not can_pin:
        raise ValueError("GC requires durable backup pin tables before backup")
    # Only control metadata is written. The customer-data snapshot remains one
    # repeatable READ ONLY transaction. Pre-migration backup works with GC off.
    run_id=begin_backup(sessions,"암호화 애플리케이션 백업과 독립 복원 검증") if can_pin else None
    try:
        content=BytesIO();manifest={"created_at":datetime.now().isoformat(),"tables":{},"objects":{},"known_unavailable_exports":[]}
        with engine.connect() as connection,connection.begin(),ZipFile(content,"w",ZIP_DEFLATED) as archive:
            if engine.dialect.name=="postgresql": connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            available=set(inspect(connection).get_table_names());meta=MetaData()
            for table in Base.metadata.sorted_tables:
                if table.name not in available: continue
                reflected=Table(table.name,meta,autoload_with=connection)
                rows=[dict(row) for row in connection.execute(select(reflected)).mappings()]
                raw=json.dumps(rows,default=encode,ensure_ascii=False,sort_keys=True).encode()
                archive.writestr("tables/"+table.name+".json",raw)
                manifest["tables"][table.name]={"rows":len(rows),"sha256":sha256(raw).hexdigest()}
                for row in rows:
                    keys=[]
                    if table.name in {"assets","approval_evidence","font_assets"} and not (row.get("metadata_json") or {}).get("_retention"): keys.append(row["storage_key"])
                    if table.name=="jobs" and row.get("result") and row["result"].get("storage_key") and not row["result"].get("_retention"):
                        integrity=row['result'].get('_integrity') or {}
                        if integrity.get('state')=='unavailable':
                            # Loss evidence, original receipt and snapshot remain
                            # in tables/jobs.json. Never archive corrupt bytes as
                            # a replacement for the declared original artifact.
                            manifest['known_unavailable_exports'].append({'job_id':row['id'],'reason':integrity.get('reason'),'confirmed_at':integrity.get('confirmed_at')})
                        else:keys.append(row["result"]["storage_key"])
                    for object_key in keys:
                        if object_key in manifest["objects"]: continue
                        data=storage.get(object_key);archive_name="objects/"+str(len(manifest["objects"]))
                        archive.writestr(archive_name,data)
                        manifest["objects"][object_key]={"path":archive_name,"sha256":sha256(data).hexdigest(),"bytes":len(data)}
            archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False).encode())
        # The global run barrier was active throughout enumeration/copy. Pin
        # writes follow the read transaction, avoiding SQLite reader deadlocks.
        if run_id: add_backup_pins(sessions,run_id,manifest["objects"])
        encrypted=Fernet(key).encrypt(content.getvalue());(output/"application-backup.fernet").write_bytes(encrypted)
        report=verify(output,key_path)
        if run_id: finish_backup(sessions,run_id,verified=True,manifest_hash=sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest())
        report["source_backup_pin_registered"]=bool(run_id)
        report["source_database_control_metadata_written"]=bool(run_id)
        (output/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        return report
    except Exception:
        if run_id: finish_backup(sessions,run_id,verified=False)
        raise
    finally:
        engine.dispose()


def verify(output,key_path,restore_dir=None):
    raw=Fernet(key_path.read_bytes()).decrypt((output/"application-backup.fernet").read_bytes())
    if restore_dir is not None:
        restore_dir=restore_dir.resolve()
        if output.resolve().is_relative_to(restore_dir) or restore_dir.is_relative_to(output.resolve()) or key_path.resolve().is_relative_to(restore_dir):
            raise ValueError("Recovery directory must be separate from backup and encryption key")
        restore_dir.mkdir(parents=True,exist_ok=False)
        settings=Settings(environment="test",database_url=f"sqlite:///{(restore_dir/'restored.db').as_posix()}",storage_backend="local",storage_dir=restore_dir/"storage")
        storage=LocalStorage(settings.storage_dir)
    else:
        settings=Settings(environment="test",database_url="sqlite:///:memory:")
        storage=None
    engine,_=build_database(settings);Base.metadata.create_all(engine)
    try:
        with ZipFile(BytesIO(raw)) as archive,engine.begin() as connection:
            # Credit compensation buckets reference older buckets in the same table;
            # SQL row order is not guaranteed. Validate all FKs after restoration.
            connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
            manifest=json.loads(archive.read("manifest.json"))
            if set(manifest["tables"])-set(Base.metadata.tables): raise ValueError("Backup contains an unsupported application table")
            # No extractall: validate every object key before copying any bytes.
            if storage:
                for object_key in manifest["objects"]: storage.path(object_key)
            count=0
            for table in Base.metadata.sorted_tables:
                if table.name not in manifest["tables"]: continue
                info=manifest["tables"][table.name];data=archive.read("tables/"+table.name+".json")
                if sha256(data).hexdigest()!=info["sha256"]: raise ValueError("Backup table checksum mismatch")
                rows=decode_rows(json.loads(data),table)
                for row in rows: connection.execute(table.insert().values(**row))
                restored=list(connection.execute(select(table)).mappings())
                if len(restored)!=info["rows"]: raise ValueError("Restored row count mismatch")
                # Compare each source column, not only row counts.
                keys=[column.name for column in table.primary_key]
                by_key={tuple(str(r[k]) for k in keys):r for r in restored}
                for row in rows:
                    actual=by_key[tuple(str(row[k]) for k in keys)]
                    for key,value in row.items():
                        other=actual[key]
                        matches=value.replace(tzinfo=None)==other.replace(tzinfo=None) if isinstance(value,datetime) else value==other
                        if not matches: raise ValueError("Restored column value mismatch")
                count+=len(rows)
            objects=0
            for object_key,info in manifest["objects"].items():
                data=archive.read(info["path"])
                if len(data)!=info["bytes"] or sha256(data).hexdigest()!=info["sha256"]: raise ValueError("Backup object checksum mismatch")
                if storage:
                    storage.put(object_key,data,"application/octet-stream")
                    if sha256(storage.get(object_key)).hexdigest()!=info["sha256"]: raise ValueError("Restored object checksum mismatch")
                objects+=1
            if list(connection.exec_driver_sql("PRAGMA foreign_key_check")): raise ValueError("Restored foreign key violation")
    finally:
        engine.dispose()
    report={"verified":True,"tables":len(manifest["tables"]),"rows_restored":count,"objects_verified":objects,"known_unavailable_exports":len(manifest.get('known_unavailable_exports',[])),"restore_target":"isolated local SQLite and copied object storage" if restore_dir else "isolated SQLite in-memory, application-level logical recovery","postgres_physical_restore_tested":False}
    if restore_dir: (restore_dir/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True);parser.add_argument("--key-file",type=Path,required=True);parser.add_argument("--verify-only",action="store_true")
    parser.add_argument("--restore-dir",type=Path,help="New private local recovery directory; never overwrites an existing directory")
    parser.add_argument("--qa-credentials",type=Path,help="Explicit JSON email selector for an isolated offline test session; not Google authentication; never printed")
    args=parser.parse_args()
    if args.qa_credentials and not args.restore_dir: parser.error("--qa-credentials requires --restore-dir")
    if args.restore_dir and not args.verify_only: parser.error("--restore-dir requires --verify-only")
    try:
        report=verify(args.output,args.key_file,args.restore_dir) if args.verify_only else backup(args.output,args.key_file)
        if args.qa_credentials:
            from verify_restored_app import verify_restored_app
            report["application"]=verify_restored_app(args.restore_dir,args.qa_credentials)
        print(json.dumps(report))
    except Exception as exc:
        # SQL parameters, credentials and source rows must never reach console.
        print(json.dumps({"verified":False,"error_type":type(exc).__name__,"message":"Backup or local recovery verification failed; customer content was not changed. Backup control metadata may record the failed attempt."}))
        raise SystemExit(1) from None
