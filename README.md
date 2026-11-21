# ChatBot
## LINE Chat Bot by using LINE messaging API and Gemini API

* LINE messaging API - https://developers.line.biz/en/services/messaging-api/
* Gemini API - https://aistudio.google.com/docs
* The programming language - Python 3.11 or newer
* Currently supported language - Japanese

You can demo the chat bot by adding the bot to a LINE friend (LINE Id is **`@594ocqcv`**).

## Environment variables

| Name | Value |
| --- | --- |
| `LINE_CHANNEL_SECRET` | Channel secret of the LINE Messaging API channel |
| `LINE_CHANNEL_ACCESS_TOKEN` | Channel access token of the same channel |
| `GEMINI_API_KEY` | API key of the Gemini API project that pays for the replies |

The server does not start when one of them is missing.

## Local verification

```sh
python3 -m venv .venv
.venv/bin/pip install -r LINE/requirements.txt
PYTHONPATH=LINE .venv/bin/python -m unittest discover -s LINE/tests -v
```

Tests use synthetic credentials and replace only outgoing HTTP transport. No LINE or Gemini API requests are sent. Missing or invalid webhook signatures are rejected before any reply is sent.
