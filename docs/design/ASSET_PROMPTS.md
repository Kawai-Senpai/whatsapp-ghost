# WhatsApp Ghost: brand guide and asset prompts

This file is the brief for generating every raster asset the redesigned Ghost
UI uses. Feed each prompt block to the image model as written. Every asset is a
PNG with a real alpha channel unless the spec says "opaque".

UI icons (send, tick, search, gear and so on) stay as the inline SVG sprite in
`console.html` and `phone.html`. Do not generate them as images: SVG stays
sharp at every zoom level and follows the theme colour.

## 1. Brand

**Name.** Ghost. Long form "WhatsApp Ghost" is only a description ("a ghost of
the WhatsApp Cloud API"). The product is a local sandbox that behaves like Meta,
so it is never styled to look like Meta or WhatsApp.

**Hard rule: no third-party marks.** No WhatsApp phone-in-bubble logo, no Meta
infinity, no Facebook "f", no green-and-white WhatsApp bubble. The existing
`meta.svg` and `whatsapp.svg` in `web/assets` go away in the redesign.

**Personality.** A calm, slightly playful lab instrument. It is precise like a
developer tool and a little warm like a friendly ghost. It never looks spooky,
Halloween, cute-kawaii or childish.

**Mark.** A soft ghost silhouette whose lower edge is a chat-bubble tail instead
of the usual wavy hem. Two small round eyes. One clean shape, readable at 16 px.

### Palette

| Token | Light | Dark | Use |
|---|---|---|---|
| `--ink` | `#0E1726` | `#E8ECF5` | Main text |
| `--ink-muted` | `#5B6478` | `#98A2B8` | Secondary text |
| `--canvas` | `#F4F6FB` | `#0B111C` | Page background |
| `--surface` | `#FFFFFF` | `#131B2A` | Cards, panels |
| `--line` | `#E1E5EE` | `#232E42` | Borders |
| `--spectral` | `#16B98C` | `#3DDBB0` | Primary: actions, delivered, brand |
| `--spectral-soft` | `#DDF7EE` | `#12382F` | Primary tint backgrounds |
| `--aura` | `#7B6CF6` | `#9D92FF` | Accent: highlights, the ghost glow |
| `--warn` | `#C98A12` | `#F5B547` | Pending, throttled |
| `--danger` | `#D9475F` | `#F0607A` | Rejected, failed |

Asset colours must come from this table. Spectral mint and aura violet are the
only saturated colours; everything else is ink, mist and white.

### Type

Inter for UI (400, 500, 600, 700), JetBrains Mono for ids, payloads and
code. Ghost runs offline, so fonts are never fetched from a CDN: the stack uses
Inter when it is installed and falls back to system-ui. To ship Inter, add the
woff2 files under `web/assets/fonts/` with an `@font-face` in `ghost-theme.css`.

### UX principles (from the premium-apps research)

These decide where illustration effort goes:

1. **Aesthetic halo needs usability under it.** Polish only pays off when the
   tool is also fast and clear. Assets never block content, and every image is
   lazy-loaded and has explicit width and height so nothing shifts.
2. **Peak-end.** Put the one delightful moment where developers feel success:
   the first webhook delivered and the first template approved. That is where
   the "peak" illustrations go. Everyday screens stay quiet.
3. **Honest progress, no fake urgency.** Illustrations for pending or review
   states show waiting calmly. Never use countdowns, scarcity or alarm imagery
   that Meta's real behaviour does not justify.
4. **Defaults carry the experience.** Empty states always point at the next
   action ("Create a customer", "Send your first template"), so each empty-state
   illustration sits above one primary button.
5. **Remove ambiguity.** Status art (rejected, paused, throttled) must read at a
   glance without the caption: shape and colour, not decoration.

## 2. Global style block

Prepend this to every prompt below:

> Flat-dimensional vector illustration, soft rounded geometry, gentle
> two-tone shading with a subtle inner glow, no outlines thicker than 2 px at
> 512 px size. Palette strictly limited to: deep ink #0E1726, mist #F4F6FB,
> white #FFFFFF, spectral mint #16B98C / #3DDBB0, aura violet #7B6CF6 /
> #9D92FF, with small amber #F5B547 or rose #F0607A accents only when stated.
> Calm, precise, friendly developer-tool aesthetic, like a premium Mac utility.
> Isolated subject on a fully transparent background (real alpha channel), no
> backdrop, no floor shadow plane, no frame, no text, no letters, no logos, no
> watermark. Centred with even padding of about 8% on all sides.

Global negative prompt:

> text, letters, numbers, watermark, signature, WhatsApp logo, Meta logo,
> Facebook logo, phone-in-speech-bubble icon, brand marks, Halloween, skulls,
> horror, scary, kawaii, chibi, 3D render, photorealism, gradient mesh noise,
> drop shadow on background, white background, checkerboard pattern, border,
> frame, cropped subject

**Note on checkerboards:** some models paint a fake transparency checkerboard.
Reject any output whose corner pixels are not alpha 0.

## 3. Asset list

Put files under `src/whatsapp_ghost/web/assets/` at the path shown. "@2x"
means also export the same art at double size with `@2x` in the name.

### 3.1 Brand mark

| File | Size | Notes |
|---|---|---|
| `brand/ghost-mark.png` | 512x512 | Master mark |
| `brand/ghost-mark-64.png` | 64x64 | Sidebar and header |
| `brand/ghost-mark-mono.png` | 512x512 | Single colour ink, for dark-on-light print |
| `brand/app-icon.png` | 1024x1024, opaque | Rounded-square app tile |
| `favicon-32.png`, `favicon-180.png` | 32, 180 | Favicon and Apple touch icon |

**ghost-mark.png**

> A single soft ghost silhouette in spectral mint #16B98C with a faint aura
> violet #7B6CF6 glow along its right edge. The ghost's lower edge is a smooth
> chat-bubble tail curving to the bottom left instead of a wavy hem. Two small
> round ink #0E1726 eyes, slightly above centre, no mouth. Shape must read
> clearly as a ghost and as a speech bubble at 16 px. Symmetrical except the
> tail.

**ghost-mark-mono.png**: same prompt, "entirely in deep ink #0E1726, eyes cut
out as transparent holes, no glow".

**app-icon.png** (opaque):

> The ghost mark from above, centred on a rounded-square tile with a smooth
> vertical gradient from #0E1726 at the bottom to #1B2740 at the top, a very
> soft aura violet halo behind the ghost. Ghost occupies 62% of the tile.

Favicons: downscale `ghost-mark.png`; at 32 px drop the glow.

### 3.2 Empty states (each 640x480 @2x, transparent)

Each sits above a heading and one primary button.

| File | Screen | Prompt subject |
|---|---|---|
| `empty/no-customers.png` | Phone roster is empty | The ghost holding an empty contact card with a small mint plus sign hovering beside it |
| `empty/no-chat-selected.png` | Phone, no chat open | The ghost floating beside a stack of three blank chat bubbles, looking toward the left |
| `empty/no-messages.png` | Chat with no history | A single empty chat bubble with a small sparkle, the ghost peeking from behind it |
| `empty/no-templates.png` | Template list empty | The ghost holding a blank document with dotted placeholder lines and two curly-brace shapes |
| `empty/no-webhooks.png` | Webhook log empty | The ghost holding a small antenna, three faint concentric signal arcs, nothing received yet |
| `empty/no-results.png` | Search with no match | The ghost holding a magnifying glass over an empty area, a tiny question mark shape made of dots (no text) |
| `empty/offline.png` | Server unreachable | The ghost holding an unplugged cable end, calm expression, amber #F5B547 spark at the plug |

Prompt template:

> [subject]. The ghost is the brand ghost: soft mint #16B98C silhouette, chat
> bubble tail hem, two round ink eyes, no mouth. Props in mist #F4F6FB and white
> with ink #0E1726 details and one aura violet #7B6CF6 accent. Wide composition,
> subject fills about 70% of the width.

### 3.3 Peak moments (each 480x480 @2x, transparent)

Shown once, in a small celebratory toast or card, then never again for that
event. This is the "peak" in peak-end; keep it to these three.

| File | Trigger | Subject |
|---|---|---|
| `peak/first-delivery.png` | First webhook delivered with a 2xx | The ghost giving a small cheer with two mint check marks floating above like confetti, a few violet sparkles |
| `peak/template-approved.png` | First template reaches APPROVED | The ghost holding a document with a large mint check seal on it |
| `peak/all-clear.png` | Template batch has zero validation errors | The ghost relaxed, lying on a small cloud of chat bubbles, tiny mint sparkles |

### 3.4 Status spots (each 128x128 @2x, transparent)

Used beside the status pill on template and webhook rows. Must read without the
caption.

| File | Status | Subject |
|---|---|---|
| `status/approved.png` | APPROVED | Mint circle with a bold white check |
| `status/pending.png` | PENDING / IN_REVIEW | Aura violet circle containing a calm white hourglass |
| `status/rejected.png` | REJECTED | Rose #F0607A circle with a white cross, slight tilt |
| `status/paused.png` | PAUSED / throttled | Amber #F5B547 circle with two white vertical bars |
| `status/failed-delivery.png` | Webhook non-2xx | Rose circle containing a white broken link |

### 3.5 Customer avatars (12 files, 256x256, transparent)

`avatars/ghost-01.png` to `avatars/ghost-12.png`. The roster assigns one by
hashing the wa_id, so the same customer always gets the same avatar.

> Circular avatar badge, flat colour disc filling the frame, with a small
> friendly ghost head-and-shoulders in white in the centre. Variation [N]:
> [variation].

Variations: 1 mint disc; 2 violet disc; 3 ink disc; 4 amber disc; 5 rose disc;
6 mint disc, ghost wearing round glasses; 7 violet disc, ghost with headphones;
8 ink disc, ghost with a tiny scarf; 9 amber disc, ghost with a cap; 10 rose
disc, ghost with a bow; 11 mint disc, ghost waving; 12 violet disc, ghost
winking.

### 3.6 Textures

Textures are subtle. If you can see the pattern from across the room, it is too
strong.

| File | Size | Spec |
|---|---|---|
| `texture/chat-wallpaper-light.png` | 512x512, seamless tile, transparent | Line-art doodle pattern of tiny chat bubbles, clocks, check marks, curly braces, envelopes and small ghosts, single colour ink #0E1726, drawn at full opacity; the CSS applies it at 5% opacity |
| `texture/chat-wallpaper-dark.png` | 512x512, seamless tile, transparent | Same doodles in mist #E8ECF5; CSS applies it at 4% |
| `texture/grain.png` | 256x256, seamless tile, transparent | Fine monochrome film grain, black specks on transparent, used at 3% over the canvas to stop large flat areas banding |
| `texture/aura-glow.png` | 1200x800, transparent | A very soft, blurred aura violet and spectral mint radial glow, off-centre top right, fading to fully transparent at all edges. Used behind the dashboard header |

Wallpaper prompt:

> Seamless tileable pattern, the left edge must continue the right edge and the
> top edge the bottom edge. Sparse, evenly scattered line-art doodles, 1.5 px
> uniform stroke at 512 px, about 40 doodles per tile, generous empty space
> between them, random rotation. Single flat colour [colour], no fill, no
> shading, transparent background.

Check seamlessness by tiling 3x3 before accepting.

### 3.7 Onboarding hero (1600x900 @2x, transparent)

`hero/onboarding.png`, shown on the empty dashboard and the integration guide.

> Wide scene: on the left, a minimal laptop showing abstract code lines (no
> readable text); in the centre, the brand ghost floating and relaying a chat
> bubble; on the right, a minimal smartphone receiving the bubble. A dashed mint
> path connects the three. Calm, airy, lots of transparent space.

## 4. Export checklist

- PNG-32, sRGB, real alpha. Corner pixels alpha 0 (except `app-icon.png`).
- No baked text. Captions are HTML so they translate and stay crisp.
- Run every file through a lossless optimiser (oxipng `-o4` or pngquant at
  quality 90-100) and keep each under: marks 40 KB, empty states 120 KB,
  textures 60 KB, hero 300 KB.
- Name files exactly as listed; the CSS and JS reference these paths.
- Light and dark: all transparent assets except the wallpapers must work on
  both `#F4F6FB` and `#0B111C`. Check each on both before accepting.
