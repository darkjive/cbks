import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ApiKeyProvider, useApiKey } from "./api/ApiKeyContext";
import { ToastProvider, useToast } from "./components/Toast";
import { ApiKeyPrompt } from "./components/ApiKeyPrompt";
import { GraphCanvas } from "./components/GraphCanvas";
import { NodeDetailPanel } from "./components/NodeDetailPanel";
import { SearchBar } from "./components/SearchBar";
import { AskPanel } from "./components/AskPanel";
import { UploadForm } from "./components/UploadForm";
import { StatsBar } from "./components/StatsBar";
import { EventLogPanel } from "./components/EventLogPanel";
import { AnalysisPanel } from "./components/AnalysisPanel";
import {
  BrainLogo,
  ColumnsIcon,
  UploadIcon,
  SearchIcon,
  BoltIcon,
  ListIcon,
  KeyIcon,
} from "./components/icons";
import { ApiError, apiFetch, apiFetchBlob } from "./api/client";
import type { GraphResponse, Node, Edge, NodeDetailResponse, SearchHit, NodeType } from "./api/types";
import { NODE_TYPE_COLORS } from "./graph/colors";

const TTS_STORAGE_KEY = "cbks-tts-enabled";
type AudioState = "idle" | "loading" | "playing";

const ALL_NODE_TYPES = Object.keys(NODE_TYPE_COLORS) as NodeType[];

function Dashboard() {
  const { apiKey } = useApiKey();
  const { pushToast, pushError } = useToast();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedNode, setSelectedNode] = useState<NodeDetailResponse | null>(null);
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [view, setView] = useState<"graph" | "analysis" | "chat">("graph");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 768
  );
  // Single-Select Filter: null = alle Typen sichtbar, sonst nur der gewählte.
  const [selectedType, setSelectedType] = useState<NodeType | null>(null);
  const visibleTypes = useMemo(
    () => (selectedType === null ? new Set(ALL_NODE_TYPES) : new Set([selectedType])),
    [selectedType]
  );
  const [ttsEnabled, setTtsEnabled] = useState<boolean>(() => {
    const stored = localStorage.getItem(TTS_STORAGE_KEY);
    return stored === null ? true : stored === "true";
  });
  const [audioState, setAudioState] = useState<AudioState>("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioObjectUrlRef = useRef<string | null>(null);
  const playRequestRef = useRef(0);

  const loadGraph = useCallback(async () => {
    try {
      const graph = await apiFetch<GraphResponse>("/graph");
      setNodes(graph.nodes);
      setEdges(graph.edges);
    } catch (err) {
      pushError(err, "Graph konnte nicht geladen werden");
    }
  }, [pushError]);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  const handleNodeSelect = useCallback(
    async (id: string) => {
      try {
        const detail = await apiFetch<NodeDetailResponse>(`/nodes/${id}`);
        setSelectedNode(detail);
      } catch (err) {
        pushError(err, "Node konnte nicht geladen werden");
      }
    },
    [pushError]
  );

  const triggerRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
    loadGraph();
  }, [loadGraph]);

  const handleDeleteNode = useCallback(async () => {
    if (!selectedNode) return;
    const id = selectedNode.node.id;
    try {
      await apiFetch<void>(`/nodes/${id}`, { method: "DELETE" });
      setSelectedNode(null);
      setSearchHits((prev) => prev.filter((hit) => hit.node.id !== id));
      triggerRefresh();
      pushToast("Node gelöscht", "success");
    } catch (err) {
      pushError(err, "Löschen fehlgeschlagen");
    }
  }, [selectedNode, triggerRefresh, pushToast, pushError]);

  const toggleType = useCallback((t: NodeType) => {
    setSelectedType((prev) => (prev === t ? null : t));
  }, []);

  const expandTo = useCallback((sectionId: string) => {
    setSidebarCollapsed(false);
    requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  const toggleTts = useCallback(() => {
    setTtsEnabled((prev) => {
      const next = !prev;
      localStorage.setItem(TTS_STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  const stopAudio = useCallback(() => {
    playRequestRef.current++;
    audioRef.current?.pause();
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
    audioRef.current = null;
    setAudioState("idle");
  }, []);

  const playNode = useCallback(
    async (id: string) => {
      stopAudio();
      const requestId = ++playRequestRef.current;
      setAudioState("loading");
      try {
        const blob = await apiFetchBlob(`/nodes/${id}/audio`);
        if (playRequestRef.current !== requestId) return;
        const url = URL.createObjectURL(blob);
        audioObjectUrlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        const releaseUrl = () => {
          if (audioObjectUrlRef.current === url) {
            URL.revokeObjectURL(url);
            audioObjectUrlRef.current = null;
          }
        };
        audio.onended = () => {
          releaseUrl();
          if (playRequestRef.current === requestId) setAudioState("idle");
        };
        audio.onerror = () => {
          releaseUrl();
          if (playRequestRef.current === requestId) setAudioState("idle");
        };
        await audio.play();
        if (playRequestRef.current === requestId) setAudioState("playing");
      } catch (err) {
        if (playRequestRef.current !== requestId) return;
        setAudioState("idle");
        if (err instanceof ApiError && err.status === 422) return;
        pushError(err, "Notiz konnte nicht vorgelesen werden");
      }
    },
    [pushError, stopAudio]
  );

  useEffect(() => {
    const id = selectedNode?.node.id;
    const content = selectedNode?.node.content;
    if (id && ttsEnabled && content) {
      playNode(id);
    } else {
      stopAudio();
    }
    return () => {
      stopAudio();
    };
    // Absichtlich nur auf die Node-ID getriggert (nicht auf ttsEnabled/content/playNode/
    // stopAudio) - das Umschalten des Toggles soll die schon offene Notiz nicht
    // rueckwirkend stoppen/starten, nur zukuenftige Knoten-Klicks.
  }, [selectedNode?.node.id]);

  return (
    <div className={`app ${sidebarCollapsed ? "sidebar-collapsed" : "sidebar-expanded"}`}>
      {!sidebarCollapsed && (
        <div className="sidebar-scrim" onClick={() => setSidebarCollapsed(true)} />
      )}
      <aside className={`app-sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
        <div className="sidebar-head">
          <div className="brand">
            <BrainLogo size={40} />
            {!sidebarCollapsed && (
              <div className="brand-text">
                <h1>CBKS</h1>
                <span className="brand-sub">Cognitive Brain Knowledge System</span>
              </div>
            )}
          </div>
          <button
            className="sidebar-toggle"
            onClick={() => setSidebarCollapsed((c) => !c)}
            title={sidebarCollapsed ? "Sidebar ausklappen" : "Sidebar einklappen"}
          >
            <ColumnsIcon size={20} />
          </button>
        </div>

        {sidebarCollapsed ? (
          <div className="sidebar-rail">
            {apiKey === null && (
              <button className="rail-icon" onClick={() => expandTo("api-key-section")} title="API-Key">
                <KeyIcon size={20} />
              </button>
            )}
            <button className="rail-icon" onClick={() => expandTo("upload-section")} title="Eingabe">
              <UploadIcon size={20} />
            </button>
            <button className="rail-icon" onClick={() => expandTo("search-section")} title="Suche">
              <SearchIcon size={20} />
            </button>
            <button className="rail-icon" onClick={() => expandTo("actions-section")} title="Aktionen">
              <BoltIcon size={20} />
            </button>
            <button className="rail-icon" onClick={() => expandTo("log-section")} title="Event-Log">
              <ListIcon size={20} />
            </button>
          </div>
        ) : (
          <>
            <div className="sidebar-section" id="filter-section">
              <h2>Filter</h2>
              <div className="filter-list">
                {ALL_NODE_TYPES.map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`legend-item ${selectedType === t ? "active" : selectedType === null ? "" : "inactive"}`}
                    onClick={() => toggleType(t)}
                    title={selectedType === t ? `Filter aufheben` : `Nur ${t} anzeigen`}
                  >
                    <span className="legend-dot" style={{ background: NODE_TYPE_COLORS[t] }} />
                    {t}
                  </button>
                ))}
              </div>
            </div>

            {apiKey === null && (
              <div className="sidebar-section" id="api-key-section">
                <ApiKeyPrompt />
              </div>
            )}

            <div className="sidebar-section" id="upload-section">
              <h2>Eingabe</h2>
              <UploadForm onIngested={triggerRefresh} />
            </div>

            <div className="sidebar-section" id="search-section">
              <h2>Suche</h2>
              <SearchBar onResults={setSearchHits} resultCount={searchHits.length} />
            </div>

            <div className="sidebar-section" id="actions-section">
              <h2>Aktionen</h2>
              <StatsBar
                refreshKey={refreshKey}
                onGraphChanged={triggerRefresh}
                ttsEnabled={ttsEnabled}
                onToggleTts={toggleTts}
              />
            </div>

            <div className="sidebar-section" id="log-section">
              <h2>Event-Log</h2>
              <EventLogPanel refreshKey={refreshKey} onGraphChanged={triggerRefresh} />
            </div>
          </>
        )}
      </aside>

      <main className="app-main">
        <div className="main-toolbar">
          <button
            className={`toolbar-tab ${view === "graph" ? "active" : ""}`}
            onClick={() => setView("graph")}
          >
            Gehirn
          </button>
          <button
            className={`toolbar-tab ${view === "analysis" ? "active" : ""}`}
            onClick={() => setView("analysis")}
          >
            Analyse
          </button>
          <button
            className={`toolbar-tab ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
        </div>
        {view === "graph" ? (
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            highlightedNodeIds={searchHits.map((hit) => hit.node.id)}
            visibleTypes={visibleTypes}
            onNodeSelect={handleNodeSelect}
            selectedNodeId={selectedNode?.node.id ?? null}
            onDeselectNode={() => setSelectedNode(null)}
          />
        ) : view === "analysis" ? (
          <AnalysisPanel refreshKey={refreshKey} />
        ) : (
          <AskPanel />
        )}
      </main>

      <NodeDetailPanel
        detail={selectedNode}
        edges={edges}
        onClose={() => setSelectedNode(null)}
        onDelete={handleDeleteNode}
        audioState={audioState}
        onPlayAudio={() => selectedNode && playNode(selectedNode.node.id)}
        onStopAudio={stopAudio}
      />

      {apiKey === null && (
        <span className="connection-status badge badge-warn">API-Key fehlt</span>
      )}
    </div>
  );
}

export function App() {
  return (
    <ApiKeyProvider>
      <ToastProvider>
        <Dashboard />
      </ToastProvider>
    </ApiKeyProvider>
  );
}
