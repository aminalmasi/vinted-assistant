# vinted-assistant

Watches Vinted filters for a specific shoe, or for things like it, and sends a
Telegram alert when something genuinely fits.

    poll (GitHub Actions)  ->  SigLIP2 floor (CPU)  ->  screen (Claude, your plan)
                           ->  final judge (Claude, your plan)  ->  Telegram

This repository is public and holds code only. Watches, prompts, reference
photos and state live on the cluster under `vinted_assistant_data/`; filter URLs
reach Actions through the `WATCH_FILTERS` secret under opaque ids.
