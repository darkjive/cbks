from backend.services.dispatcher import Dispatcher, ProcessSummary
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex


async def rebuild(
    event_log: EventLog,
    graph: GraphBackend,
    faiss_index: FaissIndex,
    dispatcher: Dispatcher,
) -> ProcessSummary:
    graph.clear_all()
    faiss_index.clear()
    events = list(event_log.replay_all())
    # Bekannte Einschraenkung: rebuild() rekonstruiert Node-Content/-Vektoren/
    # -Entity-Kanten korrekt aus dem Event-Log, aber vault-spezifischer
    # Zustand (metadata["file_hash"], "links_to"-Wiki-Link-Kanten) lebt
    # ausserhalb des Event-Replay-Pfads (siehe vault_index._finalize/
    # _sync_wikilinks) und geht bei einem Rebuild verloren. Auf einer
    # Vault-Datenbank nach cbks rebuild zusaetzlich `cbks index --full`
    # ausfuehren, um beides wiederherzustellen.
    return await dispatcher.process_events(events)
