<script lang="ts">
	const { size = 40 }: { size?: number } = $props();
</script>

<!--
  Archival Hollinger box: open lid with three documents fanning upward.
  Flat design — navy box, parchment papers, gold accent band.
-->
<svg
	width={size}
	height={size}
	viewBox="0 0 64 64"
	xmlns="http://www.w3.org/2000/svg"
	aria-label="ASAP logo — archival box"
	role="img"
>
	<!-- Box body -->
	<rect x="6" y="30" width="52" height="26" rx="2" fill="#1B2A4A" />
	<!-- Gold accent stripe -->
	<rect x="6" y="48" width="52" height="4" fill="#C9A227" />
	<!-- Box shading — front face highlight -->
	<rect x="6" y="30" width="52" height="5" rx="2" fill="#243660" />

	<!-- Left lid flap -->
	<path d="M6 30 L6 18 L30 18 L32 30 Z" fill="#1B2A4A" />
	<!-- Right lid flap -->
	<path d="M58 30 L58 18 L34 18 L32 30 Z" fill="#243660" />

	<!-- Paper 1 (left) -->
	<rect x="18" y="6" width="9" height="37" rx="1" fill="#FAF8F5" />
	<line x1="20" y1="10" x2="25" y2="10" stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="20" y1="13" x2="25" y2="13" stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="20" y1="16" x2="25" y2="16" stroke="#D5C9BA" stroke-width="1.2" />

	<!-- Paper 2 (center, tallest) -->
	<rect x="28" y="3" width="8" height="40" rx="1" fill="#F2EDE6" />
	<line x1="30" y1="7"  x2="34" y2="7"  stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="30" y1="10" x2="34" y2="10" stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="30" y1="13" x2="34" y2="13" stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="30" y1="16" x2="34" y2="16" stroke="#D5C9BA" stroke-width="1.2" />

	<!-- Paper 3 (right) -->
	<rect x="37" y="8" width="9" height="35" rx="1" fill="#FAF8F5" />
	<line x1="39" y1="12" x2="44" y2="12" stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="39" y1="15" x2="44" y2="15" stroke="#D5C9BA" stroke-width="1.2" />
	<line x1="39" y1="18" x2="44" y2="18" stroke="#D5C9BA" stroke-width="1.2" />
</svg>
