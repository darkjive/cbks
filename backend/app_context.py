import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any

from backend.config import Config
from backend.services.agents.prefrontal import LLMClient, OllamaLLMClient, PrefrontalAgent
from backend.services.agents.temporal import OllamaEmbeddingClient, TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.services.sentiment import HybridSentiment, SentimentClient
from backend.services.vision import OllamaVLMClient, VLMClient
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


@dataclass
class AppContext:
    config: Config
    conn: sqlite3.Connection
    event_log: EventLog
    graph: GraphBackend
    faiss_index: FaissIndex
    temporal_agent: TemporalAgent
    prefrontal_agent: PrefrontalAgent
    entity_resolver: EntityResolver
    dispatcher: Dispatcher
    vlm_client: VLMClient
    sentiment: SentimentClient
    llm_client: LLMClient


logger = logging.getLogger(__name__)

# Sentinel: unterscheidet "noch nicht geladen" von "geladen, aber nicht verfuegbar".
# Ohne ihn wuerde ein fehlender germansentiment-Import bei jedem Request neu versucht.
_UNSET = object()
_sentiment_model: Any = _UNSET
_sentiment_model_lock = threading.Lock()


def _get_sentiment_model() -> Any:
    # Prozess-weites Singleton: from_pretrained() ist nicht thread-sicher
    # (transformers' Meta-Device-Fast-Init crasht bei parallelen Aufrufen),
    # und build_context() wird pro Request neu ausgefuehrt.
    global _sentiment_model
    if _sentiment_model is _UNSET:
        with _sentiment_model_lock:
            if _sentiment_model is _UNSET:
                try:
                    from germansentiment import SentimentModel  # lazy: lädt torch + Modell nur wenn gebraucht

                    _sentiment_model = SentimentModel()
                except Exception as exc:  # noqa: BLE001 - optionale Abhaengigkeit
                    # Ohne germansentiment (bzw. ohne heruntergeladenes Modell) laeuft
                    # CBKS weiter: HybridSentiment faellt automatisch auf die
                    # LLM-Schaetzung zurueck. Kein Grund, den Start zu blockieren.
                    logger.warning(
                        "BERT-Sentiment nicht verfuegbar, nutze LLM-Fallback: %s", exc
                    )
                    _sentiment_model = None
    return _sentiment_model


def build_context(check_same_thread: bool = True) -> AppContext:
    config = Config.from_env()
    conn = get_connection(config.database_path, check_same_thread=check_same_thread)
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=config.embedding_dim, index_path=config.faiss_index_path)
    temporal_agent = TemporalAgent(OllamaEmbeddingClient(config.ollama_host, config.embedding_model))
    llm_client = OllamaLLMClient(config.ollama_host, config.llm_model)
    vlm_client = OllamaVLMClient(config.ollama_host, config.vlm_model)
    prefrontal_agent = PrefrontalAgent(llm_client)
    entity_resolver = EntityResolver(graph, temporal_agent, llm_client)
    sentiment = HybridSentiment(_get_sentiment_model(), llm_client)
    dispatcher = Dispatcher(
        event_log, graph, faiss_index, temporal_agent, prefrontal_agent,
        entity_resolver, config.embedding_model, sentiment=sentiment,
    )
    return AppContext(
        config=config, conn=conn, event_log=event_log, graph=graph, faiss_index=faiss_index,
        temporal_agent=temporal_agent, prefrontal_agent=prefrontal_agent,
        entity_resolver=entity_resolver, dispatcher=dispatcher, vlm_client=vlm_client,
        sentiment=sentiment, llm_client=llm_client,
    )
