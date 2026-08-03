interface ImportMetaEnv {
	readonly VITE_STREAM_API_KEY: string | undefined;
	readonly VITE_AI_ASSISTANT_URL: string | undefined;
}

interface ImportMeta {
	readonly env: ImportMetaEnv;
}
