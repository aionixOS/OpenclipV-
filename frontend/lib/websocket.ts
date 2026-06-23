import { useEffect, useState, useRef } from "react";
import { ProgressUpdate } from "./types";

export interface ProgressState extends ProgressUpdate {
    connected: boolean;
    logs: string[];
}

export function useProjectProgress(projectId: string | null): ProgressState {
    const [stage, setStage] = useState<string>("Initializing...");
    const [percent, setPercent] = useState<number>(0);
    const [message, setMessage] = useState<string>("");
    const [connected, setConnected] = useState<boolean>(false);
    const [logs, setLogs] = useState<string[]>([]);

    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    useEffect(() => {
        if (!projectId) {
            setStage("Initializing...");
            setPercent(0);
            setMessage("");
            setLogs([]);
            setConnected(false);
            return;
        }

        setStage("Initializing...");
        setPercent(0);
        setMessage("");
        setLogs([]);

        let isMounted = true;
        const host = window.location.hostname;
        const isReplit = host !== 'localhost' && host !== '127.0.0.1';
        const wsBase = isReplit
            ? `wss://${host.replace(/^(\d+)-/, '8000-')}`
            : 'ws://localhost:8000';
        const WS_URL = `${wsBase}/ws/progress/${projectId}`;

        const connect = async () => {
            if (!isMounted) return;

            try {
                // Because Vercel does not support WebSockets through Rewrites (and browsers block ws:// on https://),
                // we use standard HTTP polling which flows perfectly through the Vercel proxy rewrite.
                const res = await fetch(`/api/progress/${projectId}`);
                if (res.ok) {
                    setConnected(true);
                    const data: ProgressUpdate = await res.json();
                    
                    if (data.stage) setStage(data.stage);
                    if (data.percent) setPercent(data.percent);

                    if (data.message && data.message !== message) {
                        setMessage(data.message);
                        setLogs(prev => {
                            // Don't duplicate the same consecutive message
                            if (prev.length > 0 && prev[prev.length - 1].includes(data.message!)) return prev;
                            const newLog = `[${new Date().toLocaleTimeString()}] ${data.message}`;
                            return [...prev, newLog].slice(-50);
                        });
                    }
                } else {
                    setConnected(false);
                }
            } catch (err) {
                console.error("[Progress] Polling Error:", err);
                setConnected(false);
            }

            if (isMounted) {
                reconnectTimeoutRef.current = setTimeout(connect, 1000);
            }
        };

        connect();

        return () => {
            isMounted = false;
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current);
            }
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [projectId]);

    return { stage, percent, message, connected, logs };
}
