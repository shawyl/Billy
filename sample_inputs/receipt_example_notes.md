# Receipt Image Workflow Notes

Use a clear, front-facing receipt image with item names and totals visible.

Expected bot behavior:

1. The bot reads the image with the configured local Ollama vision model.
2. The bot replies with a readable extracted bill.
3. The bot asks for GST/service charge handling if those values are missing.
4. The user confirms or corrects the bill.
5. The user sends split instructions.
6. The bot runs five validation passes before returning a final split.

