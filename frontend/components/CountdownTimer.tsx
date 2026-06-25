'use client';

import React, { useState, useEffect } from 'react';

interface CountdownTimerProps {
    createdAt: string; // ISO string or SQLite timestamp from backend
    hoursUntilExpire?: number;
    onExpire?: () => void;
}

export function CountdownTimer({ 
    createdAt, 
    hoursUntilExpire = 2,
    onExpire 
}: CountdownTimerProps) {
    const [timeLeft, setTimeLeft] = useState<string>('--:--:--');
    const [isExpired, setIsExpired] = useState<boolean>(false);
    const [isDanger, setIsDanger] = useState<boolean>(false);

    useEffect(() => {
        if (!createdAt) return;
        
        // Parse the SQLite timestamp. SQLite uses UTC, so we append 'Z' if it doesn't have it
        let parseStr = createdAt;
        if (!parseStr.includes('T') && !parseStr.includes('Z')) {
            parseStr = parseStr.replace(' ', 'T') + 'Z';
        } else if (!parseStr.includes('Z')) {
            parseStr = parseStr + 'Z';
        }

        const createdTime = new Date(parseStr).getTime();
        const expireTime = createdTime + (hoursUntilExpire * 60 * 60 * 1000);

        const updateTimer = () => {
            const now = new Date().getTime();
            const diff = expireTime - now;

            if (diff <= 0) {
                setIsExpired(true);
                setTimeLeft('Expired');
                if (onExpire) onExpire();
                return;
            }

            // Calculate hours, minutes, seconds
            const h = Math.floor(diff / (1000 * 60 * 60));
            const m = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
            const s = Math.floor((diff % (1000 * 60)) / 1000);

            // Danger mode if less than 15 minutes left
            setIsDanger(diff < 15 * 60 * 1000);

            setTimeLeft(
                `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
            );
        };

        // Run immediately then every second
        updateTimer();
        const interval = setInterval(updateTimer, 1000);

        return () => clearInterval(interval);
    }, [createdAt, hoursUntilExpire, onExpire]);

    if (isExpired) {
        return (
            <div className="flex items-center gap-1.5 text-xs font-bold text-red-500 bg-red-500/10 px-2 py-1 rounded-md border border-red-500/20">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                Deleted
            </div>
        );
    }

    return (
        <div className={`flex items-center gap-1.5 text-xs font-mono font-bold px-2 py-1 rounded-md border transition-colors ${
            isDanger 
                ? 'text-orange-400 bg-orange-500/10 border-orange-500/30' 
                : 'text-slate-400 bg-slate-800/50 border-slate-700/50'
        }`} title="Time until auto-deletion">
            <svg className={`w-3.5 h-3.5 ${isDanger ? 'animate-pulse' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            {timeLeft}
        </div>
    );
}
