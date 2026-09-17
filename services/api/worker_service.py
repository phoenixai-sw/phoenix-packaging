"""One dispatcher shared by the native worker, Celery and protected cron."""
import logging
from . import models, feature_models
from .billing import models as billing_models
from .jobs import process_pending_jobs
from .ai_jobs import process_ai_jobs
from .production_jobs import process_production_jobs
from .editable_exports import process_editable_jobs


def process_all_jobs(sessions,storage,settings):
    counts={}
    from .billing.outbox import deliver_outbox
    try: counts["outbox"]=deliver_outbox(sessions)
    except Exception as exc:
        logging.getLogger("phoenix.worker").error("outbox_dispatch_failed error_type=%s",type(exc).__name__)
        counts["outbox"]="retry_pending"
    for kind,run in (("review",lambda:process_pending_jobs(sessions,storage,limit=1)),("editable",lambda:process_editable_jobs(sessions,storage,limit=1)),("production",lambda:process_production_jobs(sessions,storage,settings,limit=1)),("images",lambda:process_ai_jobs(sessions,storage,settings,limit=1))):
        try: counts[kind]=run()
        except Exception as exc:
            logging.getLogger("phoenix.worker").error("worker_dispatch_failed kind=%s error_type=%s",kind,type(exc).__name__)
            counts[kind]="retry_pending"
    from .billing.payments import BillingSettings, build_provider, process_due_invoices, reconcile_pending_orders
    billing=BillingSettings(environment=settings.environment)
    if billing.capabilities()["checkout_available"]:
        try:
            provider=build_provider(billing)
            counts["reconciled"]=reconcile_pending_orders(sessions,provider=provider,settings=billing)
            counts["invoices"]=process_due_invoices(sessions,provider=provider,settings=billing,limit=5)
        except Exception as exc:
            logging.getLogger("phoenix.worker").error("billing_dispatch_failed error_type=%s",type(exc).__name__)
            counts["invoices"]="retry_pending"
    from .uploads import cleanup_quarantine
    try: counts["uploads_cleaned"]=cleanup_quarantine(sessions,storage)
    except Exception as exc:
        logging.getLogger("phoenix.worker").error("quarantine_cleanup_failed error_type=%s",type(exc).__name__)
        counts["uploads_cleaned"]="retry_pending"
    from .asset_reconciliation import reconcile_ai_assets
    from .font_assets.routes import cleanup_font_quarantine
    try: counts['font_uploads_cleaned']=cleanup_font_quarantine(sessions,storage)
    except Exception:
        counts['font_uploads_cleaned']='retry_pending'
    try: counts["assets_checked"]=reconcile_ai_assets(sessions,storage)
    except Exception as exc:
        logging.getLogger("phoenix.worker").error("asset_integrity_check_failed error_type=%s",type(exc).__name__)
        counts["assets_checked"]="retry_pending"
    from .export_reconciliation import reconcile_exports
    try:counts['exports_checked']=reconcile_exports(sessions,storage)
    except Exception as exc:
        logging.getLogger('phoenix.worker').error('export_integrity_check_failed error_type=%s',type(exc).__name__)
        counts['exports_checked']='retry_pending'
    from .retention.service import refresh_notices
    from .retention.storage_lifecycle import process_known_orphans
    from .retention.deletion import process_deletion_requests
    try:
        counts["retention_checked"] = refresh_notices(sessions, limit=20)
        gc = process_known_orphans(sessions, storage, settings)
        counts["orphan_candidates_checked"], counts["orphan_files_deleted"] = gc["checked"], gc["deleted"]
        requested = process_deletion_requests(sessions, storage, settings)
        counts["deletion_requests_checked"], counts["requested_files_deleted"] = requested["checked"], requested["deleted"]
    except Exception as exc:
        logging.getLogger("phoenix.worker").error("retention_dispatch_failed error_type=%s", type(exc).__name__)
        counts["retention_checked"] = "retry_pending"
    return counts
