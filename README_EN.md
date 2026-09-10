# 🎭 AI Murder Mystery Game (JubenSha)

> 2026-09-08: M3 is ready for local user validation with the fixed human role T and four AI roles. A real complete game reached all five sealed submissions and settlement, with 15 verified recovery points; the local entry is http://127.0.0.1:18032. Known retelling, certainty, citation and repetitive-phone issues remain. User acceptance, M4, voice and formal publication are pending. See the [current M3 record](docs/development/M3_CONTINUOUS_VALIDATION.md); older status statements below are historical.

> Current single-human Fusion development: see [development handoff](docs/development/README.md).
> Historical feature descriptions below are not proof of current runtime readiness. Never overwrite an existing .env.

> Admin authoring tasks are available at `/admin/authoring-jobs`. On 2026-09-06, real Compiler v4 and Audit v3 calls completed the source verification and candidate workflow for a synthetic case with two characters and two phases. The audit covered all five categories, with two findings and no blockers. Earlier failures remain recorded. This result applies only to that small case; it does not establish commercial-script or full-product readiness, or grant publication approval. See the [3C real validation record](docs/development/PHASE_3_RULE_CONTRACT_REVIEW.md).
>
> [Phase 3D](docs/development/PHASE_3_PUBLICATION_BINDING.md) now adds human confirmation bound to all review evidence, explicit handling of model findings, and a separate publication action at `/admin/script-reviews`. Immutable releases support a fixed-role opening preview at `/play/package-preview`. Legacy direct PUBLISHED paths are blocked. Later review changes prevent new openings for that release and require a new content version; existing opening sessions retain their original version. This remains an opening preview with `runtime_ready=false`; full rules, AI interaction and complete play are not connected. This phase made no paid model calls, processed no commercial text and approved no real candidate. The new migration has not been applied to real PostgreSQL.
>
> Phase 3D validation: 1085 backend tests and 89 frontend tests passed, along with full TypeScript checks, lint for changed files and a 24-page production build. An isolated synthetic browser fixture passed human confirmation, separate publication, a regular player's fixed-role opening, reload persistence and access checks. Both admin and player pages fit 390px without horizontal overflow. No new model attempts occurred.
>
> Earlier [Phase 4A](docs/development/PHASE_4_RULES_PREVIEW.md) adds a deterministic fixed-role stage rehearsal at `/play/package-flow`. Users explicitly create it from an opening after a fresh release check; the opening remains read-only. Stages advance manually, with material access controlled by the phase floor and all required public evidence. Players may explicitly share their own unlocked MAY_SHARE/MUST_SHARE material; KEEP_PRIVATE remains protected. An invalidated release freezes new actions while existing authorized views and original action replays remain readable. This is still `runtime_ready=false`, without AI interaction, facilitator deadlines, settlement or full gameplay. Backend 1195 and frontend 114 tests, full TypeScript, changed-file lint and the final 25-page production build passed. An isolated synthetic browser fixture passed explicit creation, sharing and dependent unlocks, stage advancement, reload and re-entry, access checks and a 390px layout without horizontal overflow. No new model calls occurred. The new migration remains unapplied to real PostgreSQL.

> Current [Phase 4B](docs/development/PHASE_4_TEXT_PLAY.md) is connected in code at `/play/package-play`. Users explicitly create an independent text-play session from their opening after a fresh release check; existing openings and stage rehearsals are unchanged. AI characters share one public record and session budget, while each receives only its own authorized context. The model selects material references; the server validates them, formally shares selected MAY_SHARE/MUST_SHARE material and renders the original text. KEEP_PRIVATE remains excluded. Players advance manually and explicitly settle at the final phase to reveal only the package's designated ending and truths. With AI unavailable, manual progression and the ending still work. See the [text-play contract](docs/contracts/package-play.md).
>
> This remains `runtime_ready=false`. Real AI quality and the new migration/concurrency on real PostgreSQL are unverified. Commercial win/loss rules, natural dialogue, facilitation and voice are not connected. Migration `o5b6c7d8e9f0` has not been applied to real PostgreSQL.
>
> 4B validation status: 1378 backend tests passed (363 existing deprecation warnings, 12.35 seconds; 183 new tests included), along with 152 tests across eight frontend suites, full TypeScript, changed-file lint with CommonJS test files explicitly configured, preflight and 16 preflight self-tests. The isolated synthetic browser fixture passed explicit creation, AI reference selection and formal sharing, human sharing with dependent unlocks, three-stage advancement, designated truth reveal, reload/re-entry, access checks and 390px layout. It produced one play, six events and one simulated SDK call, with zero real model calls. The opening was unchanged, browser space 60 was closed, and temporary API 18017/frontend 13017 exited with their ports confirmed closed. The final production build passed with 26 pages; the production API remains http://127.0.0.1:8010.

[中文版本](README.md)

An AI-powered murder mystery game system where all characters are played by AI. The project consists of both frontend and backend components, built with modern technology stacks.

## 🌟 Key Features

- 🤖 **Full AI Character Play** - All characters are driven by AI with unique backgrounds and secrets
- 🎯 **Complete Game Flow** - Includes background introduction, self-introduction, evidence collection, investigation, discussion, voting, truth revelation, and other complete stages
- 🌐 **Real-time Synchronization** - Uses WebSocket to achieve real-time game state synchronization
- 💻 **Modern Interface** - Responsive web interface with mobile access support
- 🧠 **Intelligent Reasoning Engine** - AI reasoning capabilities based on large language models
- 🔊 **TTS Voice Broadcasting** - Text-to-speech support for enhanced immersive experience
- 🎨 **AI Image Generation** - Supports AI-generated character avatars, evidence images, and scene pictures
- ✏️ **AI Script Editing** - Supports AI generation and editing of script content

## 📸 Screenshots

<div style="display: flex; flex-direction: column; gap: 20px;">
  <div style="display: flex; justify-content: space-between; gap: 20px;">
    <img src="screenshot/game_room.png" alt="Game Room Interface" style="width: 48%;">
    <img src="screenshot/scripts_center.png" alt="Scripts Center Interface" style="width: 48%;">
  </div>
  <div style="display: flex; justify-content: space-between; gap: 20px;">
    <img src="screenshot/edit_script.png" alt="Edit Script Interface" style="width: 48%;">
    <img src="screenshot/script_create.png" alt="Script Creation Interface" style="width: 48%;">
  </div>
</div>

## 🚀 Core Functions

### AI Script Generation and Editing
- Automatically generates complete murder mystery content, including background stories, character settings, evidence design, and scene descriptions
- Supports natural language commands to edit scripts, such as "add a kind character" or "modify the murderer's motive"
- Allows随时 adjustment of script content, with AI automatically adapting to modifications while maintaining logical consistency

### AI Plot Deduction
- Fully automatic AI-driven game flow without human participation
- 8 game stages: background introduction, self-introduction, evidence collection, investigation, free discussion, voting, truth revelation, and game end
- Each AI character has unique personality and secrets, capable of natural dialogue and reasoning

### TTS Voice Synthesis
- MiniMax API: Supports multiple languages and voices
- [CosyVoice 2.0](https://github.com/journey-ad/CosyVoice2-Ex): Local deployment of Chinese voice synthesis service

### Text-to-Image Services
Supports multiple image generation services:
- ComfyUI: Locally deployed stable diffusion image generation platform
- MiniMax API: Cloud-based image generation service

### Large Language Model (LLM) Support
Compatible with all OpenAI API-compatible large language models

## 📁 Project Structure

```
jubensha/
├── backend/     # Backend services
│   ├── src/     # Core source code
│   ├── docs/    # Documentation
│   └── tests/   # Test code
└── frontend/    # Frontend interface
    ├── src/     # Frontend source code
    └── public/  # Static resources
```

## 🚀 Quick Start

### Environment Preparation

#### Backend Services
- Python 3.13+
- uv (Python package manager)
- PostgreSQL database
- At least one AI service API key (OpenAI compatible models, TTS services, image generation services, etc.)

#### Frontend Interface
- Node.js 18+
- npm or yarn

### Configuration and Running

#### 1. Backend Service Configuration and Running

1. **Enter backend directory**
   ```bash
   cd backend
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Configure environment variables**
   
   Copy and edit the `.env` file:
   ```bash
   test -e .env || cp .env.example .env
   # Edit the .env file to set your API keys and other configurations
   ```
   
   Configure necessary API keys in the `.env` file:
   - `OPENAI_API_KEY` or other LLM service keys
   - `TTS_API_KEY` and `TTS_PROVIDER` (such as cosyvoice2-ex or minimax)
   - `MINIMAX_API_KEY` (if using MiniMax image generation or TTS services)
   - Database connection information

4. **Initialize database**
   ```bash
   # Perform database migration and initialization according to project documentation
   ```

5. **Run backend service**
   ```bash
   uv run python main.py
   ```

#### 2. Frontend Interface Configuration and Running

1. **Enter frontend directory**
   ```bash
   cd frontend
   ```

2. **Install dependencies**
   ```bash
   npm install
   ```

3. **Configure environment variables**
   
   Copy and edit the `.env` file:
   ```bash
   test -e .env || cp .env.example .env
   # Edit the .env file to set backend API address
   ```

4. **Run frontend development server**
   ```bash
   npm run dev
   ```

5. **Build production version**
   ```bash
   npm run build
   npm run start
   ```

## 🎮 Game Flow

1. **Background Introduction Stage** - System narrates the case background story
2. **Self-introduction Stage** - AI characters introduce their identity and background one by one
3. **Evidence Collection Stage** - AI characters search scenes and discover evidence
4. **Investigation Stage** - AI characters ask each other questions to advance investigation
5. **Free Discussion Stage** - AI characters share reasoning and counter arguments
6. **Voting Stage** - AI characters vote to identify the murderer
7. **Truth Revelation Stage** - Announce the case truth and game results

## 🛠 Technical Architecture

### Backend Technology Stack
- **FastAPI** - Modern, fast (high-performance) web framework
- **LangChain** - Framework for building AI applications
- **OpenAI/Compatible Models** - Large language model support
- **PostgreSQL** - Relational database
- **WebSocket** - Real-time bidirectional communication
- **MinIO** - Object storage service

### Frontend Technology Stack
- **Next.js 16** - React framework
- **React 19** - Frontend UI library
- **TypeScript** - JavaScript superset
- **Tailwind CSS** - CSS framework
- **Zustand** - State management
- **Radix UI** - Unstyled component library

## 📝 Development Plan

Future optimization directions include:

### Script Generation and Editing Features
- Enhance AI script generation capabilities to support more complex plot structures
- Optimize script editing experience with more intuitive editing interface
- Support more types of script templates and customization options
- Improve AI's ability to maintain script logical consistency

### UI Interaction Optimization
- Improve user interface design to enhance user experience
- Optimize mobile adaptation and interaction effects
- Enhance visual feedback during gameplay
- Provide smoother operation flow and animation effects

### Multilingual Support
- Support internationalization and multilingual switching
- Support multiple languages including Chinese and English

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
You are free to:
- Use this software for commercial purposes
- Modify and distribute this software
- Use part or all of this software's code in your projects

## 🤝 Contribution

Welcome to submit Issues and Pull Requests to improve the project.
