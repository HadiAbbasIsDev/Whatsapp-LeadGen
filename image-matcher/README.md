# Decor Moments image matcher

Create the isolated environment, install the CPU-only PyTorch wheels, and then
install the matcher package in this order:

```powershell
python -m venv image-matcher/.venv
image-matcher/.venv/Scripts/python -m pip install --upgrade pip
image-matcher/.venv/Scripts/python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu
image-matcher/.venv/Scripts/python -m pip install -e "image-matcher[dev]"
```
