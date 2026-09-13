# AI壁打ち

同じ問いを ChatGPT、Claude、Gemini に送り、回答と考え方の違いを縦に読み比べる個人用アプリです。これは「正解」や「ベスト回答」を決めるものではなく、自分で考える材料を集めるためのツールです。

## 初回セットアップ

### 1. Python を確認する

ターミナルを開き、次を実行します。

```bash
python3 --version
```

Python 3.10 以上なら進めます。

### 2. 必要なライブラリを入れる

このフォルダに移動して、次を実行します。

```bash
python3 -m pip install -r requirements.txt
```

### 3. APIキーを設定する

`.streamlit/secrets.toml.example` をコピーして、同じ `.streamlit` フォルダ内に `secrets.toml` を作成します。次の空欄へ、それぞれのサービスで取得したAPIキーを入れます。

```toml
OPENAI_API_KEY = "..."
ANTHROPIC_API_KEY = "..."
GEMINI_API_KEY = "..."
APP_PASSWORD = "好きな暗証番号"
SUPABASE_URL = "https://プロジェクトID.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."
```

`secrets.toml` は `.gitignore` に含まれています。**APIキー入りのこのファイルは絶対にGitHubへアップロードしないでください。**

`APP_PASSWORD` は公開したアプリを開くための暗証番号です。APIキーとは別の、推測されにくい文字列を設定してください。

`SUPABASE_URL` と `SUPABASE_SECRET_KEY` は履歴保存に使います。未設定でもAIへの質問はできますが、アプリを閉じた後に履歴は残りません。Secret keyは管理者権限を持つため、APIキーと同様にGitHubや画面へ絶対に公開しないでください。

### 4. 履歴保存を設定する

1. Supabaseでプロジェクトを作成します。
2. Supabaseの「SQL Editor」を開きます。
3. [`supabase_schema.sql`](supabase_schema.sql) の内容を貼り付けて実行します。
4. Project URLとSecret keyを、上記のSecretsへ設定します。

### 5. アプリを起動する

```bash
streamlit run app.py
```

表示されたURLをブラウザで開きます。終了するときはターミナルで `Ctrl + C` を押します。

## 使い方

1. 「いま何について考えたい？」へ質問を書きます。
2. 「3つのAIに聞く」を押します。
3. 3つの回答を読み、その下の「回答の違い」で共通点・差分・次に考える問いを確認します。
4. 「過去の質問を見る」から、以前の質問と回答を開けます。

一部のAPIが失敗しても、成功したAIの回答は表示されます。2つ以上の回答が得られたときだけ比較分析を行います。

## Streamlit Community Cloud で公開する

1. このフォルダをGitHubの新しいリポジトリへアップロードします。`secrets.toml` が含まれていないことを確認します。
2. [Streamlit Community Cloud](https://share.streamlit.io/) にログインし、「Create app」を選びます。
3. GitHubのリポジトリ、ブランチ、`app.py` を選んで公開します。
4. アプリ設定の「Secrets」を開き、ローカルと同じ3つのAPIキー、`APP_PASSWORD`、Supabaseの2項目を貼り付けて保存します。
5. 公開URLを開き、短い質問で動作を確認します。

## 注意点

- 初期モデル名は `config.py` にまとめています。提供元で利用可能なモデル名へ変更してください。
- 1回の質問で、3つの回答取得と比較分析を合わせて最大4回のAPI呼び出しを行います。
- 暗証番号を知っている人は同じ履歴を閲覧できます。個人用として、暗証番号を共有しないでください。
- 本格的なユーザー認証、ファイル添付、Web検索、回答の統合・順位付けは V0.1 の対象外です。
