import { Project, Clip, Settings, CaptionStyle } from './types';

function getBaseUrl(): string {
    if (typeof window !== 'undefined') {
        // In the browser, use relative paths to hit Next.js proxy rewrites
        return "";
    }
    // Server-side (during SSR or static generation)
    return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
}

export function getApiBaseUrl(): string {
    return getBaseUrl();
}

export function getUserId(): string {
    if (typeof window === 'undefined') return 'server';
    let userId = localStorage.getItem('openclip_user_id');
    if (!userId) {
        userId = typeof crypto !== 'undefined' && crypto.randomUUID 
            ? crypto.randomUUID() 
            : Math.random().toString(36).substring(2) + Date.now().toString(36);
        localStorage.setItem('openclip_user_id', userId);
    }
    return userId;
}

export const getProjects = async (): Promise<Project[]> => {
    try {
        const res = await fetch(`${getBaseUrl()}/api/projects`, { 
            cache: 'no-store',
            headers: { 'x-user-id': getUserId() }
        });
        if (!res.ok) return [];
        return res.json();
    } catch {
        // Backend not running or network error - return empty array
        return [];
    }
};

export const getProject = async (id: string): Promise<Project & { clips: Clip[] }> => {
    const res = await fetch(`${getBaseUrl()}/api/projects/${id}`, { 
        cache: 'no-store',
        headers: { 'x-user-id': getUserId() }
    });
    if (!res.ok) throw new Error("Project not found");
    return res.json();
};

export const createProject = async (youtube_url: string): Promise<{ project_id: string }> => {
    const res = await fetch(`${getBaseUrl()}/api/projects`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'x-user-id': getUserId()
        },
        body: JSON.stringify({ youtube_url })
    });
    if (!res.ok) throw new Error("Failed to create project");
    return res.json();
};

export const deleteProject = async (id: string): Promise<void> => {
    const res = await fetch(`${getBaseUrl()}/api/projects/${id}`, {
        method: 'DELETE',
        headers: { 'x-user-id': getUserId() }
    });
    if (!res.ok) throw new Error("Failed to delete project");
};

export async function getSettings(): Promise<Settings> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/settings`, {
            headers: { 'x-user-id': getUserId() }
        });
        if (!res.ok) {
            // Return default settings if backend returns error
            return {
                llm_provider: "openai",
                llm_model: "gpt-4o",
                whisper_model: "base",
                caption_style: "viral_word",
                has_api_key: false
            };
        }
        return res.json();
    } catch {
        // Backend not running - return default settings
        return {
            llm_provider: "openai",
            llm_model: "gpt-4o",
            whisper_model: "base",
            caption_style: "viral_word",
            has_api_key: false
        };
    }
}

export async function saveSettings(
    settings: Partial<Settings> & { llm_api_key?: string }
): Promise<void> {
    const res = await fetch(`${getBaseUrl()}/api/settings`, {
        method: "POST",
        headers: { 
            "Content-Type": "application/json",
            "x-user-id": getUserId()
        },
        body: JSON.stringify(settings)
    });
    if (!res.ok) {
        let errMsg = "Failed to save settings";
        try {
            const errData = await res.json();
            errMsg = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail || errData);
        } catch {
            const text = await res.text().catch(() => "");
            if (text) errMsg = text;
        }
        throw new Error(errMsg);
    }
}

export async function getCaptionStyles(): Promise<CaptionStyle[]> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/caption-styles`, {
            headers: { 'x-user-id': getUserId() }
        });
        if (res.ok) return res.json();
    } catch {
    }
    return [
        {
            key: "none",
            name: "No Captions",
            animation: "word_by_word",
            preview_colors: { text: "#666666", highlight: null, background: null }
        },
        {
            key: "classic_white",
            name: "Classic White",
            animation: "word_by_word",
            preview_colors: { text: "#FFFFFF", highlight: null, background: null }
        },
        {
            key: "tiktok_style",
            name: "TikTok Style",
            animation: "highlight",
            preview_colors: { text: "#FFFFFF", highlight: "#FFFF00", background: null }
        },
        {
            key: "viral_word",
            name: "Viral Word",
            animation: "one_word",
            preview_colors: { text: "#FFFFFF", highlight: null, background: null }
        }
    ];
}

export const uploadProject = async (file: File): Promise<{ project_id: string }> => {
    const formData = new FormData();
    formData.append("file", file);
    
    const res = await fetch(`${getBaseUrl()}/api/projects/upload`, {
        method: 'POST',
        headers: {
            'x-user-id': getUserId()
            // Do not set Content-Type for FormData, browser will set it with boundary
        },
        body: formData
    });
    if (!res.ok) {
        let errMsg = "Failed to upload project";
        try {
            const errData = await res.json();
            errMsg = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail || errData);
        } catch {
            const text = await res.text().catch(() => "");
            if (text) errMsg = text;
        }
        throw new Error(errMsg);
    }
    return res.json();
};
