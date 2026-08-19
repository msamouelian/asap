import { apiStream } from './client';

export interface RagChunk {
	id: string;
	text: string;
	page_no: number | null;
	heading: string | null;
	file_name: string;
	collection: string;
}

export type SseEvent =
	| { type: 'conversation_id'; conversation_id: string }
	| { type: 'rag_context';     chunks: RagChunk[]; search_query: string | null }
	| { type: 'thinking_token';  content: string }
	| { type: 'thinking_done' }
	| { type: 'token';           content: string }
	| { type: 'tool_start';      tool: string; args?: string }
	| { type: 'tool_result';     tool: string; summary: string }
	| { type: 'usage';           prompt_tokens: number; context_pct: number }
	| { type: 'done' }
	| { type: 'error';           detail: string };

export async function* streamChat(
	message: string,
	conversationId?: string,
	signal?: AbortSignal,
	documentCollectionIds?: string[],
	temperature?: number,
): AsyncGenerator<SseEvent> {
	const res = await apiStream(
		'/chat',
		{
			message,
			conversation_id: conversationId,
			document_collection_ids: documentCollectionIds ?? [],
			temperature,
		},
		signal,
	);
	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buffer = '';

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });

		// SSE lines: "data: <json>\n\n"
		const parts = buffer.split('\n\n');
		buffer = parts.pop() ?? '';

		for (const part of parts) {
			const line = part.trim();
			if (!line.startsWith('data:')) continue;
			const json = line.slice(5).trim();
			if (!json) continue;
			try {
				yield JSON.parse(json) as SseEvent;
			} catch {
				// malformed chunk — skip
			}
		}
	}
}
