# PaperClip – Local Clipboard AI Agent


## Description

A lightweight Python-based personal AI agent that automatically reads prompts from the clipboard, sends them to an AI model provider (OpenRouter or any OpenAI-compatible API), receives the response, minimalizes it (code-only output), and copies it back to the clipboard.

Designed for fast coding workflows, minimal noise, and zero UI overhead. Runs locally in a terminal and uses simple JSON file storage.

## Project Purpose 
PaperClip allows you to:

- Copy a prompt beginning with "agent.prompt" and automatically get an AI-generated code answer in your clipboard.
- Enable “agent.promptall” mode to send every clipboard copy as a prompt (hands-free).
- Keep the same chat session even if models switch or API providers fail.
- Use multiple API keys and automatic failover (best model first).
- Use file-based storage (JSON sessions) with no database.
- Store no clipboard history (to avoid interference with restricted exam/test websites).
- Keep running even if the browser or UI is closed, as long as the terminal is open.

## System Requirements

- Python 3.8 or higher
- Internet connection
- Installed packages: aiohttp, pyperclip
- replace the placeholders GEMINI_API_KEY and/or OPENROUTER_API_KEY atleast at one place in providers.json

## Installation

Install using:
```
pip install aiohttp pyperclip
```

## File List

- **bot.py**: Main agent program. Handles clipboard monitoring, model calling, provider failover, message minimalization, and session saving.
- **providers.json**: File where the user stores multiple API provider entries. You can add any number of providers (OpenRouter, OpenAI-compatible proxies, etc). Each provider entry includes:
  - id
  - name
  - base_url
  - api_key
  - model
  - priority
  - enabled
- **sessions/**: Folder automatically created by PaperClip. Stores session JSON files (conversation history for each chat session). Clipboard history is NOT stored for privacy and exam-site restrictions.
- **README.md**: This documentation file.

## How the Agent Works

### A. Clipboard Monitoring
The agent constantly monitors the system clipboard. When clipboard text changes, it checks command patterns.

### B. Command Patterns
1. `agent.prompt <your prompt here>`: Sends the extracted prompt to the model.
2. `agent.prompt`: First copy triggers listening. The NEXT copied text becomes the actual prompt to send.
3. `agent.promptall`: Enables mode where every clipboard copy is sent automatically until disabled.
4. `agent.promptone`: Disables "promptall" mode and requires explicit "agent.prompt".
5. `model.gem`: Switch preferred provider to Gemini.
6. `model.openr`: Switch preferred provider to OpenRouter.

### C. Processing Flow
- Prompt is sent to best provider (based on priority).
- If provider fails (rate limit or error), the next provider is tried.
- Agent receives model output.
- Response is minimalized to code-only.
- Output is automatically copied back to clipboard.
- Response + prompt are saved to a session JSON file.

### D. Session Handling
- At program start, a new session is created.
- You can manually create new sessions using the "new" terminal command.
- Each session is stored separately in ./sessions/.

## Running the Program

1. Ensure providers.json exists with valid API keys.
2. Install required packages:
   ```
   pip install aiohttp pyperclip
   ```
3. Run the agent:
   ```
   python bot.py
   ```
4. Leave the terminal open.
5. Use clipboard commands to interact with the agent.

## Terminal Commands (Type in the PaperClip Terminal)

- `h`: Show help menu.
- `status`: Show current mode and session info.
- `new`: Start a new chat session (saved as a new JSON file).
- `mode all`: Turn on agent.promptall (send every clipboard change).
- `mode off` / `mode one`: Disable automatic sending.
- `providers`: Show list of loaded providers.
- `quit` / `exit`: Stop the program.

## Providers.json Format Example

```json
[
  {
    "id": "kilo_api_01",
    "name": "OpenRouter - key 1",
    "type": "openai",
    "base_url": "https://api.openrouter.ai",
    "api_key": "YOUR_OPENROUTER_KEY",
    "model": "openai/gpt-4o-mini",
    "priority": 1,
    "enabled": true
  }
]
```

**Notes:**
- **priority**: Lower number = more preferred.
- Model name must match an OpenRouter supported model for free-tier usage.
- You may add as many providers as you want.

## Minimalized Response Behavior

The agent ensures you get code-first compact replies:

- Extracts code blocks (```...```) if present.
- If no code fence, heuristically extracts code-like lines.
- Removes long explanations.
- Inserts a short “// code (extracted)” header.

This makes copied results small and perfect for coding workflows.

## Safety & Privacy Notes

- Clipboard history is NOT saved.
- Only the session prompt/response text is saved.
- API keys remain in providers.json; DO NOT share that file.
- If you accidentally paste sensitive info with agent.promptall enabled, immediately disable using "agent.promptone".
- This tool does not interfere with restricted-test browsers because it does not open or modify any web tabs or automation.

## Common Errors

- **HTTP 401**: Invalid or expired API key. Rotate your OpenRouter key.
- **HTTP 400**: Likely incorrect model name. Check supported models for your free-tier account.
- **HTTP 500**: Temporary provider error. Try again or try another key/provider.
- **Clipboard not responding**: Restart the bot or disable any clipboard-blocking software.

## Future Improvements (Optional)

- GUI dashboard
- Clipboard history viewer
- Auto model performance ranking
- Token usage analytics
- Windows/Linux service mode (background daemon)

## License & Usage

This project is for personal local use. You may modify and expand it freely. Avoid uploading providers.json or sessions containing sensitive data.