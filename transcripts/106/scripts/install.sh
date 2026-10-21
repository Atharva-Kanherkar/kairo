set -e
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH=$HOME/.local/bin:$PATH
uv venv -p 3.12 /work/venv
. /work/venv/bin/activate
uv pip install "ai-dynamo[vllm]==1.6.0.dev20261019" openai httpx
python -c "import vllm, dynamo, torch; print(\"vllm\", vllm.__version__, \"torch\", torch.__version__, torch.version.cuda, torch.cuda.is_available())"
uv pip freeze | grep -i -E "^(ai-dynamo|vllm|torch|nixl|transformers|openai)" 
echo INSTALL_DONE
