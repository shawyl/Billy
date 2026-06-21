# Telegram Bill Split Bot

A local-first Telegram bot that helps split bills from receipt photos or manual bill text. A local LLM through Ollama extracts and interprets the bill, while Python performs the actual money calculation with deterministic validation.

The key rule is simple: the LLM can read and structure information, but it is never trusted to decide final payable amounts.

## What It Does

- Accepts Telegram text messages with manual bill details.
- Accepts Telegram receipt photos and image document uploads, then downloads them to a local temporary folder.
- Uses Ollama text and vision models to extract structured bill data.
- Shows the extracted bill back to the user before any split calculation.
- Always asks how to handle GST and service charge when they are missing or unclear.
- Reads GST/service charge instructions from the same message as the bill when provided.
- Converts natural language split instructions into a structured allocation plan.
- Runs split interpretation and deterministic calculation five times.
- Returns the final split only when all five rounded results match exactly.
- Sends a detailed result followed by a concise copy-friendly split summary.
- Supports `/help`, `/reset`, and `/status`.
- Asks for clarification instead of guessing when validation runs disagree.

## Why Local LLM

Receipt and bill interpretation can contain private payment and dining information. This project is designed to use locally hosted Ollama models so that bill extraction and split interpretation can happen on your own machine.

## Why Calculation Is Separate From Interpretation

LLMs are useful for reading messy human input, but they are not reliable accounting engines. This bot uses the LLM only for:

- Extracting bill fields from text or images.
- Interpreting split instructions into structured rules.

Python handles:

- Currency math with `Decimal`.
- Equal splits.
- Selected-person splits.
- Quantity-based ownership.
- Fixed item ownership.
- Proportional GST, service charge, and discount allocation.
- Rounding to two decimal places.
- Total validation.

## Bill Confirmation Flow

When a receipt photo or bill text is received, the bot first replies with a readable extracted bill:

```text
Receipt detected

I read the bill as:

1. Pizza - $24.00
2. Guinness x5 - $50.00
3. Fries - $12.00

Subtotal: $86.00
GST: Not found
Service charge: Not found
Total: $86.00

Please confirm if this looks correct.
```

The bot does not calculate a split at this point. The user can confirm or correct the bill first.

Common confirmation replies are accepted, including `confirm`, `yes`, `ok`, `looks good`, `proceed`, `calculate`, and `go ahead`.

You can also confirm and provide split rules in the same message:

```text
Confirm

3 Guinness on me, 6 on C, 5 Suntory Gin on Y
```

For simple quantity ownership rules like this, Billy builds a deterministic split plan directly and validates the final calculation before replying.

You can also send corrections such as `Pizza is 28 not 26`, `No GST`, or `Service charge is 10%, GST is 9%`. Billy updates the current state and sends a short refreshed confirmation instead of restarting the conversation.

## GST And Service Charge Clarification

If GST or service charge is missing or unclear, the bot always asks how to handle them before splitting:

```text
Also, should I include GST and service charge?
For example:

* No GST/service charge
* Add 9% GST
* Add 10% service charge + 9% GST
* Use custom amounts
```

Calculation does not proceed until this is clear.

## Five-Pass Consensus

For final split requests, the bot runs the LLM interpretation step five independent times. Each parsed output is validated and passed to the deterministic calculator. The bot only returns a final answer when all five calculated results match exactly after currency rounding.

If the runs disagree, the bot returns a short clarification request. It does not average results, choose the most common result, or guess.

When the bot asks for a clarification about similar items with different prices, the answer is resolved into a locked internal allocation before calculation. For example:

```text
Alex: 2.5 HH Guinness, C: remaining HH Guinness + Guinness
```

If the bill contains `HH Guinness x6` and `Guinness x1`, Billy resolves this deterministically as:

- `HH Guinness`: Alex `2.5`, C `3.5`
- `Guinness`: C `1`

Locked allocations are calculated directly by Python instead of being reinterpreted by the LLM across five runs.

## Setup

1. Create and activate a Python environment.

```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Copy the environment example.

```bash
copy .env.example .env
```

4. Fill in `.env`.

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TEXT_MODEL=your_text_model
OLLAMA_VISION_MODEL=your_vision_model
```

5. Make sure Ollama is running and the configured models are available.

6. Start the bot.

```bash
python -m src.bot
```

## Windows One-Click Launch

1. Copy `.env.example` to `.env`.
2. Fill in the Telegram token and Ollama model names.
3. Make sure Ollama is running.
4. Double-click `launch_billy.bat`.

The launcher creates `.venv` if needed, installs dependencies, checks Ollama at `http://localhost:11434`, and starts the bot with `python -m src.bot`.

Ollama must be running before Billy can process LLM requests. The terminal window stays open and shows runtime logs, including five-pass validation activity.

## Environment Variables

Required:

- `TELEGRAM_BOT_TOKEN`
- `OLLAMA_BASE_URL`
- `OLLAMA_TEXT_MODEL`
- `OLLAMA_VISION_MODEL`

Optional:

- `ALLOWED_CHAT_ID`: if set, the bot only replies to that Telegram chat.
- `CONSENSUS_RUNS`: defaults to `5`.
- `LOG_LEVEL`: defaults to `INFO`.
- `THIRD_PARTY_LOG_LEVEL`: defaults to `WARNING` to suppress noisy Telegram/httpx polling logs.
- `IMAGE_DEBUG`: defaults to `false`. Set to `true` to keep extra image preprocessing variants and log sanitized vision response previews.
- `OLLAMA_VISION_FALLBACK_MODEL`: optional second vision model to try when the primary image extraction is incomplete.
- `TEMP_IMAGE_DIR`: defaults to `temp_images`.
- `DEFAULT_CURRENCY`: defaults to `SGD`.
- `DEFAULT_USER_NAME`: controls how Billy labels `me`, `I`, and `my` in split rules. If blank, Billy uses the Telegram sender's first name when available, otherwise `You`.
- `RESULT_DETAIL_LEVEL`: defaults to `normal`. Supported values are `concise_only`, `normal`, and `detailed`.

## Example Telegram Usage

Manual bill:

```text
Pizza 24
Guinness x5 50
Fries 12
Subtotal 86
Total 86
```

After the bot shows the extracted bill and GST/service prompt:

```text
No GST/service charge
```

Then send the split:

```text
There are three people: Alex, C, and Y.
Pizza is split equally with everyone.
2 Guinness are Alex's and 3 are C's.
Fries are shared by Alex and Y.
```

Manual shorthand with split instructions in the same message:

```text
1X Battered Fish Bites -> 14.00
1X Pizza -> 26.00
6X HH Guinness -> 11.00
2X Gin Tonic -> 12.00
1X Guinness -> 14.00

Me, C & Y

Battered fish bites and Pizza split equally among 3
All Gin Tonic on Y
2.5 Guinness on Me, rest on C
```

For quantity-prefix lines such as `6X HH Guinness -> 11.00`, the amount is treated as the unit price by default, so the line total is `$66.00`. If a quantity-based rule spans similarly named items with different unit prices, the bot asks for clarification before calculating.

Percentage charges are interpreted by their labels. `Add 10% GST and 9% service charge` means GST is `10%` and service charge is `9%`. Percentage GST and service charge are calculated from the subtotal using the current deterministic rule.

The detailed final result includes a validation status line such as `Validation: 5/5 matched`. The bot also writes validation details to `logs/`, including requested runs, valid runs, result hashes, and whether all canonical results matched.

After the detailed result, Billy sends a second concise message containing only the total and per-person amounts, making it easier to copy into another chat.

Result detail modes:

- `concise_only`: send only the copy-friendly split.
- `normal`: send a short detailed result plus the concise split.
- `detailed`: include full notes as well as the concise split.

Useful commands:

- `/help`: show a short input example.
- `/reset`: clear the current bill and start over.
- `/status`: show what Billy is waiting for.

## Image Bill Workflow

1. Send a clear receipt photo to the bot, or upload the receipt as an image document if Telegram compression hurts quality.
2. Review the extracted bill in Telegram.
3. Correct anything wrong, such as `Guinness should be x4, not x5`.
4. Clarify GST/service charge if needed.
5. Send split instructions.
6. Wait for the five validation runs to agree.

For better image accuracy:

- Crop to the receipt or order-summary section.
- Avoid motion blur and glare.
- Include item lines, charges, discounts, and totals.
- Use image document upload for screenshots or photos where Telegram compression makes text harder to read.

To test image extraction without Telegram:

```bash
python -m src.receipt_debug path\to\receipt.jpg
```

This preprocesses the image, calls the configured Ollama vision model, prints raw extraction attempts, prints the receipt validation result, prints normalized receipt JSON, and prints the Telegram-style confirmation message.

Useful receipt debug options:

```bash
python -m src.receipt_debug path\to\receipt.jpg --repair
python -m src.receipt_debug path\to\receipt.jpg --model qwen2.5vl:7b
python -m src.receipt_debug path\to\receipt.jpg --model llama3.2-vision:11b
```

Image extraction can be partial even when a model reads some fields correctly. Billy validates item totals against explicit subtotal or grand total values, then checks subtotal plus service charge and GST minus discount against the final payable amount. If a receipt appears incomplete, Billy retries with enhanced/cropped variants and a focused repair prompt. If it still cannot reconcile the receipt, it asks for confirmation instead of silently accepting a wrong item list.

To inspect image preprocessing and sanitized model previews, set:

```env
IMAGE_DEBUG=true
```

Billy keeps third-party Telegram/httpx polling logs quiet by default:

```env
THIRD_PARTY_LOG_LEVEL=WARNING
```

To try a secondary model only when the primary model returns an incomplete receipt:

```env
OLLAMA_VISION_FALLBACK_MODEL=llama3.2-vision:11b
```

## Manual CLI Testing

You can test the flow without Telegram:

```bash
python -m src.parser sample_inputs/manual_bill_example.txt sample_inputs/split_instruction_example.txt
```

The CLI uses the same Ollama parsing and consensus flow. If GST or service charge is missing, it prompts interactively.

## Tests

Run:

```bash
pytest
```

The tests cover calculation behavior, charge clarification, Telegram-friendly formatting, parser contracts, consensus success, consensus failure, invalid JSON, missing people, quantity mismatches, and total mismatches.

## Project Structure

```text
bill-split-bot/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── sample_inputs/
├── src/
│   ├── bot.py
│   ├── config.py
│   ├── ollama_client.py
│   ├── image_handler.py
│   ├── prompts.py
│   ├── parser.py
│   ├── conversation_state.py
│   ├── bill_confirmation.py
│   ├── consensus.py
│   ├── calculator.py
│   ├── formatter.py
│   └── models.py
└── tests/
```

## Limitations

- Receipt extraction quality depends on the configured Ollama vision model and image clarity.
- The bot keeps conversation state in memory, so state is lost when the process restarts.
- Corrections are handled with simple deterministic updates and may require restating the bill if the correction is complex.
- The consensus check reduces unsafe outputs but cannot fix ambiguous instructions. When ambiguity remains, the bot asks for clarification.
