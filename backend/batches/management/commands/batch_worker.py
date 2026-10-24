import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from batches.files import LostLease, build_export, claim_file_task, import_csv
from batches.notifications import deliver_one
from batches.partial_exports import build_partial_export, claim_partial_export, fail_partial_export
from batches.queue import claim_number, finish_number, pause_all
from batches.upstream import lookup_number

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Durable CSV queue worker. Run lookup, files and alerts roles as separate processes."

    def add_arguments(self, parser):
        parser.add_argument("--role", choices=["lookup", "files", "alerts"], required=True)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True  # Finish/checkpoint in-flight work before exiting.

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        role = options["role"]
        prefer_partial = True
        self.stdout.write(f"CSV worker ready: {role}")
        while not stopping:
            work = False
            partial = None
            try:
                close_old_connections()
                if role == "lookup":
                    claim = claim_number()
                    if claim:
                        work = True
                        finish_number(claim, lookup_number(claim["ani"]))
                elif role == "files":
                    # Alternate task types so downloads and imports cannot starve
                    # one another. Neither uses the lookup worker's HTTP slot.
                    partial = claim_partial_export() if prefer_partial else None
                    task = None if partial else claim_file_task()
                    if not task and not partial:
                        partial = claim_partial_export()
                    prefer_partial = not prefer_partial
                    if partial:
                        work = True
                        build_partial_export(partial)
                    elif task:
                        work = True
                        (import_csv if task.status == "preprocessing" else build_export)(task)
                else:
                    work = deliver_one()
            except LostLease:
                logger.warning("File worker lease changed; stale task stopped.")
            except Exception:
                logger.exception("CSV worker failure.")
                if role == "alerts" or partial is not None:
                    # Notifications and partial downloads do not control lookups.
                    if partial is not None:
                        try:
                            fail_partial_export(partial, "Unable to prepare the partial CSV. Try again.")
                        except LostLease:
                            pass
                        except Exception:
                            logger.exception("Could not persist partial export failure; its lease will expire.")
                    if options["once"]:
                        return
                    time.sleep(1)
                    continue
                try:
                    pause_all("An internal CSV worker error requires staff review.")
                except Exception:
                    logger.exception("Could not persist the safety stop. Worker is exiting.")
                    raise
            finally:
                close_old_connections()
            if options["once"]:
                return
            if not work:
                time.sleep(.5)
