from collections import defaultdict
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from .artifacts import SSCD_ARTIFACT, validate_artifact


class SscdEncoder:
    """Pinned SSCD TorchScript encoder with deterministic image preprocessing."""

    model_sha256 = SSCD_ARTIFACT.sha256

    def __init__(self, model_path: Path, device: str = "cpu") -> None:
        validate_artifact(model_path, SSCD_ARTIFACT)
        self.device = torch.device(device)
        self.model = torch.jit.load(str(model_path), map_location=self.device).eval()
        self.preprocess = transforms.Compose(
            [
                transforms.Resize(288),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=(0.485, 0.456, 0.406),
                    std=(0.229, 0.224, 0.225),
                ),
            ]
        )

    def encode(self, images: Sequence[Image.Image]) -> np.ndarray:
        """Encode PIL images to defensively L2-normalized float32 descriptors."""
        if not images:
            return np.empty((0, 512), dtype=np.float32)

        grouped: dict[tuple[int, int], list[tuple[int, torch.Tensor]]] = defaultdict(list)
        for position, image in enumerate(images):
            tensor = self.preprocess(image.convert("RGB"))
            grouped[(tensor.shape[-2], tensor.shape[-1])].append((position, tensor))

        encoded: list[np.ndarray | None] = [None] * len(images)
        with torch.inference_mode():
            for batch_items in grouped.values():
                positions, tensors = zip(*batch_items)
                batch = torch.stack(tensors).to(self.device)
                descriptors = self.model(batch)
                if not isinstance(descriptors, torch.Tensor):
                    raise ValueError("SSCD model returned a non-tensor output")
                values = descriptors.detach().to(device="cpu", dtype=torch.float32).numpy()
                if values.ndim != 2 or values.shape[0] != len(positions):
                    raise ValueError(f"SSCD model returned unexpected shape {values.shape}")
                for position, value in zip(positions, values, strict=True):
                    encoded[position] = value

        matrix = np.stack(encoded).astype(np.float32, copy=False)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if np.any(~np.isfinite(norms)) or np.any(norms == 0.0):
            raise ValueError("SSCD model returned a non-finite or zero descriptor")
        return (matrix / norms).astype(np.float32, copy=False)
