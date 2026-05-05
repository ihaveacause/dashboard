@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=JetBrains+Mono:wght@300;400;500&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #080808;
  --bg2:      #0F0F0F;
  --bg3:      #141414;
  --border:   #1C1C1C;
  --border2:  #252525;
  --text:     #F0EBE1;
  --text2:    #8A8580;
  --text3:    #4A4845;
  --green:    #1DB954;
  --green-bg: rgba(29,185,84,0.08);
  --red:      #E5484D;
  --red-bg:   rgba(229,72,77,0.08);
  --amber:    #F5A623;
  --amber-bg: rgba(245,166,35,0.08);
  --blue:     #4A90D9;
  --blue-bg:  rgba(74,144,217,0.08);
  --purple:   #9B59B6;
  --font-display: 'Syne', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}

html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--font-mono); }
a { color: inherit; text-decoration: none; }
button { cursor: pointer; font-family: var(--font-mono); }
input, textarea { font-family: var(--font-mono); }
::-webkit-scrollbar { width: 3px; height: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }
