<!--
  Same archival-box shape but with CSS-animated papers bobbing up and out.
  Used in the chat panel while the LLM is processing.
-->
<div class="flex flex-col items-center gap-2 py-4 thinking-glow" aria-label="ASAP is thinking…">
	<svg width="72" height="72" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
		<!-- Box body -->
		<rect x="6" y="30" width="52" height="26" rx="2" fill="#1B2A4A" />
		<rect x="6" y="48" width="52" height="4" fill="#C9A227" />
		<rect x="6" y="30" width="52" height="5" rx="2" fill="#243660" />

		<!-- Lid flaps -->
		<path d="M6 30 L6 18 L30 18 L32 30 Z" fill="#1B2A4A" />
		<path d="M58 30 L58 18 L34 18 L32 30 Z" fill="#243660" />

		<!-- Animated paper 1 -->
		<g class="paper-1">
			<rect x="18" y="6" width="9" height="24" rx="1" fill="#FAF8F5" />
			<line x1="20" y1="10" x2="25" y2="10" stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="20" y1="13" x2="25" y2="13" stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="20" y1="16" x2="25" y2="16" stroke="#D5C9BA" stroke-width="1.2" />
		</g>

		<!-- Animated paper 2 -->
		<g class="paper-2">
			<rect x="28" y="3" width="8" height="27" rx="1" fill="#F2EDE6" />
			<line x1="30" y1="7"  x2="34" y2="7"  stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="30" y1="10" x2="34" y2="10" stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="30" y1="13" x2="34" y2="13" stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="30" y1="16" x2="34" y2="16" stroke="#D5C9BA" stroke-width="1.2" />
		</g>

		<!-- Animated paper 3 -->
		<g class="paper-3">
			<rect x="37" y="8" width="9" height="22" rx="1" fill="#FAF8F5" />
			<line x1="39" y1="12" x2="44" y2="12" stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="39" y1="15" x2="44" y2="15" stroke="#D5C9BA" stroke-width="1.2" />
			<line x1="39" y1="18" x2="44" y2="18" stroke="#D5C9BA" stroke-width="1.2" />
		</g>
	</svg>

	<p class="text-sm text-muted italic select-none">Searching the archives…</p>
</div>
