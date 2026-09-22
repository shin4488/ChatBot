# ChatBot
## LINE Chat Bot by using LINE messaging API and Talk API

* LINE messaging API - https://developers.line.biz/en/services/messaging-api/
* Talk API - https://a3rt.recruit-tech.co.jp/about/
* The programming language - Python 3.10 or newer
* Currently supported language - Japanese

You can demo the chat bot by adding the bot to a LINE friend (LINE Id is **`@594ocqcv`**).

## Local verification

```sh
python3 -m venv .venv
.venv/bin/pip install -r LINE/requirements.txt
PYTHONPATH=LINE .venv/bin/python -m unittest discover -s LINE/tests -v
```

Tests use synthetic credentials and replace only outgoing HTTP transport. No LINE or Talk API requests are sent. Missing or invalid webhook signatures are rejected before any reply is sent.
