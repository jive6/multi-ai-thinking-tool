"""アプリ全体で使うモデル設定をまとめる場所です。"""

# モデルを変更するときは、このファイルだけを編集します。
OPENAI_MODEL = "gpt-5.6-terra"
ANTHROPIC_MODEL = "claude-sonnet-5"
GEMINI_MODEL = "gemini-3.8-flash"

# 比較分析は V0.1 では OpenAI を使います。
COMPARE_MODEL = OPENAI_MODEL

# 長めの相談でも話の途中で終わりにくい出力量にします。
# Claude は必要になれば 16_000 まで、この値だけで変更できます。
OPENAI_MAX_OUTPUT_TOKENS = 8_000
ANTHROPIC_MAX_TOKENS = 8_000
GEMINI_MAX_OUTPUT_TOKENS = 8_000

# 要約と比較を1回で構造化して返すための上限です。
COMPARE_MAX_OUTPUT_TOKENS = 8_000
