# -*- coding: utf-8 -*-
"""UniCalli generation core.

The UI consumes a small event stream and does not depend on Diffusers callback details.
"""
from __future__ import annotations

import csv
import gc
import inspect
import json
import queue
import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Literal, Optional, Sequence, Tuple

from PIL import Image, ImageOps

try:
    from inference import CalligraphyGenerator
except ImportError as exc:  # Makes static checks possible outside the project tree.
    CalligraphyGenerator = Any  # type: ignore[misc,assignment]
    _INFERENCE_IMPORT_ERROR: Optional[BaseException] = exc
else:
    _INFERENCE_IMPORT_ERROR = None


CHUNK_SIZE = 5
PAD_CHARACTER = "□"
SYNTHETIC_AUTHOR = "合成风格"
MODEL_LOCK = threading.Lock()

FONT_STYLE_NAMES = {
    "楷": "楷书",
    "行": "行书",
    "草": "草书",
}

EventType = Literal[
    "task_started",
    "segment_started",
    "preview",
    "segment_completed",
    "task_completed",
    "error",
]


@dataclass(frozen=True)
class SegmentTask:
    index: int
    source_text: str
    model_text: str
    display_text: str
    is_padded: bool


@dataclass(frozen=True)
class GenerationRequest:
    text: str
    author: Optional[str]
    font_style: str
    num_steps: int
    seed: int
    random_seed: bool
    quant_mode: str


@dataclass
class GenerationEvent:
    type: EventType
    segments: Sequence[SegmentTask]
    segment_index: Optional[int] = None
    image: Optional[Image.Image] = None
    step: Optional[int] = None
    total_steps: Optional[int] = None
    seed: Optional[int] = None
    message: str = ""
    streaming_supported: Optional[bool] = None
    preview_count: int = 0


@dataclass(frozen=True)
class ProjectData:
    author_fonts: Dict[str, List[str]]
    author_styles: Dict[str, str]

    @property
    def author_list(self) -> List[str]:
        return sorted(self.author_fonts)


def load_project_data(base_dir: Path | str = ".") -> ProjectData:
    base = Path(base_dir)
    author_fonts: Dict[str, List[str]] = {}
    excluded_fonts = {"隶", "篆"}
    csv_path = base / "dataset" / "author_fonts_summary.csv"
    with csv_path.open("r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            author = row["书法家"]
            fonts = row["字体类型"].split("|")
            supported = [font for font in fonts if font not in excluded_fonts]
            if supported:
                author_fonts[author] = supported

    styles_path = base / "dataset" / "calligraphy_styles_en.json"
    try:
        author_styles = json.loads(styles_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        author_styles = {}
    return ProjectData(author_fonts=author_fonts, author_styles=author_styles)


def is_han_character(character: str) -> bool:
    """Return True for CJK unified ideographs accepted by the writing field."""
    if not character:
        return False
    codepoint = ord(character)
    return any(
        start <= codepoint <= end
        for start, end in (
            (0x3400, 0x4DBF),   # Extension A
            (0x4E00, 0x9FFF),   # Unified Ideographs
            (0xF900, 0xFAFF),   # Compatibility Ideographs
            (0x20000, 0x2A6DF), # Extension B
            (0x2A700, 0x2B73F), # Extension C
            (0x2B740, 0x2B81F), # Extension D
            (0x2B820, 0x2CEAF), # Extension E/F
            (0x2CEB0, 0x2EBEF), # Extension F/I
            (0x30000, 0x323AF), # Extension G/H
        )
    )


def sanitize_han_text(text: str) -> str:
    """Silently discard anything that is not a Han ideograph."""
    return "".join(character for character in (text or "") if is_han_character(character))


def split_text_into_segments(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    pad_character: str = PAD_CHARACTER,
) -> List[SegmentTask]:
    """Split input into five-character model tasks and pad the final task with □."""
    normalized = sanitize_han_text(text)
    if not normalized:
        return []

    segments: List[SegmentTask] = []
    for index, start in enumerate(range(0, len(normalized), chunk_size)):
        source = normalized[start : start + chunk_size]
        padded = source + pad_character * (chunk_size - len(source))
        segments.append(
            SegmentTask(
                index=index,
                source_text=source,
                model_text=padded,
                display_text=padded,
                is_padded=len(source) < chunk_size,
            )
        )
    return segments


def resolve_font_style(font_style: str) -> str:
    for font_key, display_name in FONT_STYLE_NAMES.items():
        if display_name == font_style:
            return font_key
    raise ValueError(f"无法识别字体风格：{font_style}")


def font_choices_for_author(author: str, author_fonts: Dict[str, List[str]]) -> List[str]:
    if author == SYNTHETIC_AUTHOR or author not in author_fonts:
        return list(FONT_STYLE_NAMES.values())
    return [
        FONT_STYLE_NAMES[font]
        for font in author_fonts[author]
        if font in FONT_STYLE_NAMES
    ]


class GeneratorService:
    """Owns model lifecycle and exposes one event-stream interface."""

    def __init__(self, base_dir: Path | str = ".") -> None:
        self.base_dir = Path(base_dir)
        self.generator: Any = None
        self.quant_mode: Optional[str] = None

    def get_generator(self, quant_mode: str) -> Any:
        if _INFERENCE_IMPORT_ERROR is not None:
            raise RuntimeError(
                "无法导入 inference.CalligraphyGenerator，请在 UniCalli 项目根目录运行。"
            ) from _INFERENCE_IMPORT_ERROR

        if self.generator is not None and self.quant_mode != quant_mode:
            self.generator = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except (ImportError, RuntimeError):
                pass

        if self.generator is None:
            is_4bit = quant_mode.startswith("4-bit")
            is_8bit = quant_mode.startswith("8-bit")
            self.generator = CalligraphyGenerator(
                model_name="flux-dev",
                device="cuda",
                offload=False,
                intern_vlm_path=str(self.base_dir / "checkpoints" / "internvl_embedding"),
                checkpoint_path=str(self.base_dir / "checkpoints" / "unicalli-base_cleaned.bin"),
                font_descriptions_path=str(self.base_dir / "dataset" / "chirography.json"),
                author_descriptions_path=str(
                    self.base_dir / "dataset" / "calligraphy_styles_en.json"
                ),
                use_deepspeed=False,
                use_4bit_quantization=is_4bit,
                use_8bit_quantization=is_8bit,
                use_dual_gpu=not (is_4bit or is_8bit),
            )
            self.quant_mode = quant_mode
        return self.generator

    def stream(self, request: GenerationRequest) -> Generator[GenerationEvent, None, None]:
        segments = split_text_into_segments(request.text)
        if not segments:
            raise ValueError("请输入需要题写的文字。")

        font = resolve_font_style(request.font_style)
        base_seed = self._resolve_seed(request.seed, request.random_seed)
        generator = self.get_generator(request.quant_mode)
        total_steps = int(request.num_steps)

        yield GenerationEvent(
            type="task_started",
            segments=segments,
            seed=base_seed,
            total_steps=total_steps,
            message=f"共 {len(segments)} 段 · 起卷",
        )

        total_preview_count = 0
        streaming_segment_count = 0

        for segment in segments:
            segment_seed = base_seed + segment.index
            yield GenerationEvent(
                type="segment_started",
                segments=segments,
                segment_index=segment.index,
                seed=segment_seed,
                total_steps=total_steps,
                message=f"第 {segment.index + 1}/{len(segments)} 段 · 候墨",
            )

            kwargs = {
                "text": segment.model_text,
                "font_style": font,
                "author": request.author,
                "num_steps": total_steps,
                "seed": segment_seed,
            }

            segment_stream = generate_single_segment_stream(
                generator,
                kwargs,
                total_steps=total_steps,
            )
            while True:
                try:
                    preview_image, step = next(segment_stream)
                except StopIteration as stop:
                    final_image, streaming_supported, preview_count = stop.value
                    break

                total_preview_count += 1
                yield GenerationEvent(
                    type="preview",
                    segments=segments,
                    segment_index=segment.index,
                    image=preview_image,
                    step=step,
                    total_steps=total_steps,
                    seed=segment_seed,
                    preview_count=total_preview_count,
                    message=(
                        f"第 {segment.index + 1}/{len(segments)} 段 · 显墨 "
                        f"{min(step + 1, total_steps)}/{total_steps}"
                    ),
                )

            if streaming_supported:
                streaming_segment_count += 1
            yield GenerationEvent(
                type="segment_completed",
                segments=segments,
                segment_index=segment.index,
                image=final_image,
                seed=segment_seed,
                total_steps=total_steps,
                streaming_supported=streaming_supported,
                preview_count=preview_count,
                message=f"第 {segment.index + 1}/{len(segments)} 段 · 定墨",
            )

        if total_preview_count:
            stream_note = f"显墨预览 {total_preview_count} 帧"
        elif streaming_segment_count:
            stream_note = "已接入回调，当前管线未返回可转换帧"
        else:
            stream_note = "已按分段依次入卷"

        yield GenerationEvent(
            type="task_completed",
            segments=segments,
            seed=base_seed,
            total_steps=total_steps,
            preview_count=total_preview_count,
            message=f"题写完成 · {stream_note}",
        )

    @staticmethod
    def _resolve_seed(seed: int, random_seed: bool) -> int:
        if not random_seed:
            return int(seed)
        try:
            import torch

            return int(torch.randint(0, 2**32, (1,)).item())
        except (ImportError, RuntimeError):
            return random.randint(0, 2**32 - 1)


def coerce_pil_image(value: Any) -> Optional[Image.Image]:
    if isinstance(value, Image.Image):
        return value.convert("RGB")

    if isinstance(value, (list, tuple)) and value:
        for item in value:
            image = coerce_pil_image(item)
            if image is not None:
                return image

    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            array = value
            if array.ndim == 4:
                array = array[0]
            if array.ndim == 3 and array.shape[-1] in (3, 4):
                if array.dtype != np.uint8:
                    array = array.astype("float32")
                    if array.min() < 0:
                        array = (array + 1.0) / 2.0
                    array = np.clip(array, 0.0, 1.0)
                    array = (array * 255.0).round().astype("uint8")
                return Image.fromarray(array[..., :3]).convert("RGB")
    except (ImportError, TypeError, ValueError):
        pass

    try:
        import torch

        if torch.is_tensor(value):
            tensor = value.detach().float().cpu()
            if tensor.ndim == 4:
                tensor = tensor[0]
            if tensor.ndim == 3 and tensor.shape[0] == 3:
                tensor = tensor.permute(1, 2, 0)
                if tensor.min().item() < 0:
                    tensor = (tensor + 1.0) / 2.0
                tensor = tensor.clamp(0, 1)
                array = (tensor.numpy() * 255.0).round().astype("uint8")
                return Image.fromarray(array).convert("RGB")
    except (ImportError, RuntimeError, TypeError, ValueError):
        pass

    return None


def decode_latents_to_pil(
    pipeline: Any,
    latents: Any,
    height: Optional[int] = None,
    width: Optional[int] = None,
) -> Optional[Image.Image]:
    if pipeline is None or latents is None:
        return None
    if not hasattr(pipeline, "vae") or not hasattr(pipeline, "image_processor"):
        return None

    try:
        import torch

        if not torch.is_tensor(latents):
            return None
        with torch.no_grad():
            sample = latents.detach()
            if sample.ndim == 3 and hasattr(pipeline, "_unpack_latents"):
                scale = getattr(pipeline, "vae_scale_factor", 8)
                default_size = getattr(pipeline, "default_sample_size", 64)
                target_height = int(height or default_size * scale)
                target_width = int(width or default_size * scale)
                sample = pipeline._unpack_latents(
                    sample, target_height, target_width, scale
                )

            config = pipeline.vae.config
            scaling_factor = float(getattr(config, "scaling_factor", 1.0))
            shift_factor = float(getattr(config, "shift_factor", 0.0) or 0.0)
            sample = (sample / scaling_factor) + shift_factor

            parameter = next(pipeline.vae.parameters(), None)
            if parameter is not None:
                sample = sample.to(device=parameter.device, dtype=parameter.dtype)

            decoded = pipeline.vae.decode(sample, return_dict=False)[0]
            images = pipeline.image_processor.postprocess(decoded, output_type="pil")
            return coerce_pil_image(images)
    except (AttributeError, RuntimeError, TypeError, ValueError, StopIteration):
        return None


def find_pipeline_slot(generator: Any) -> Optional[Tuple[Any, str, Any]]:
    preferred_names = ("pipe", "pipeline", "flux_pipeline", "diffusion_pipeline")
    owners = [generator]
    try:
        for value in vars(generator).values():
            if value is None or value is generator:
                continue
            module_name = type(value).__module__.lower()
            if "torch" not in module_name and "transformers" not in module_name:
                owners.append(value)
    except TypeError:
        pass

    seen: set[int] = set()
    for owner in owners:
        if id(owner) in seen:
            continue
        seen.add(id(owner))
        names = list(preferred_names)
        try:
            names.extend(name for name in vars(owner).keys() if name not in names)
        except TypeError:
            pass
        for name in names:
            try:
                candidate = getattr(owner, name)
            except (AttributeError, RuntimeError):
                continue
            if candidate is None or not callable(candidate):
                continue
            module_name = type(candidate).__module__.lower()
            class_name = type(candidate).__name__.lower()
            if (
                hasattr(candidate, "vae")
                or "diffusers" in module_name
                or "pipeline" in class_name
            ):
                return owner, name, candidate
    return None


def extract_preview_from_callback(
    pipeline: Any,
    args: Sequence[Any],
    kwargs: Dict[str, Any],
    height: Optional[int] = None,
    width: Optional[int] = None,
) -> Optional[Image.Image]:
    preferred_keys = ("image", "images", "preview", "decoded", "decoded_image")
    for key in preferred_keys:
        if key in kwargs:
            image = coerce_pil_image(kwargs[key])
            if image is not None:
                return image

    callback_dicts = [item for item in args if isinstance(item, dict)]
    callback_dicts.extend(value for value in kwargs.values() if isinstance(value, dict))
    for callback_dict in callback_dicts:
        for key in preferred_keys:
            if key in callback_dict:
                image = coerce_pil_image(callback_dict[key])
                if image is not None:
                    return image
        for latent_key in ("latents", "latent", "sample"):
            if latent_key in callback_dict:
                image = decode_latents_to_pil(
                    pipeline,
                    callback_dict[latent_key],
                    height=height,
                    width=width,
                )
                if image is not None:
                    return image

    for item in reversed(args):
        image = coerce_pil_image(item)
        if image is not None:
            return image
    for item in reversed(args):
        image = decode_latents_to_pil(pipeline, item, height=height, width=width)
        if image is not None:
            return image
    return None


def extract_step(args: Sequence[Any], kwargs: Dict[str, Any], fallback: int = 0) -> int:
    for key in ("step", "step_index", "i"):
        value = kwargs.get(key)
        if isinstance(value, int):
            return value
    if len(args) >= 2 and isinstance(args[1], int):
        return args[1]
    if args and isinstance(args[0], int):
        return args[0]
    return fallback


def make_callbacks(
    push_preview: Callable[[Image.Image, int], None],
    pipeline: Any,
    total_steps: int,
    height: Optional[int] = None,
    width: Optional[int] = None,
) -> Tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    interval = max(1, int(total_steps) // 14)
    counter = {"value": 0}

    def maybe_push(args: Sequence[Any], kwargs: Dict[str, Any]) -> int:
        step = extract_step(args, kwargs, fallback=counter["value"])
        counter["value"] = max(counter["value"] + 1, step + 1)
        if step % interval != 0 and step + 1 < total_steps:
            return step
        image = extract_preview_from_callback(
            pipeline, args, kwargs, height=height, width=width
        )
        if image is not None:
            push_preview(image, step)
        return step

    def callback_on_step_end(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        maybe_push(args, kwargs)
        for item in reversed(args):
            if isinstance(item, dict):
                return item
        for value in kwargs.values():
            if isinstance(value, dict):
                return value
        return {}

    def legacy_callback(*args: Any, **kwargs: Any) -> None:
        maybe_push(args, kwargs)
        return None

    def generic_callback(*args: Any, **kwargs: Any) -> None:
        maybe_push(args, kwargs)
        return None

    return callback_on_step_end, legacy_callback, generic_callback


class StreamingPipelineProxy:
    def __init__(
        self,
        pipeline: Any,
        push_preview: Callable[[Image.Image, int], None],
        total_steps: int,
    ) -> None:
        object.__setattr__(self, "_pipeline", pipeline)
        object.__setattr__(self, "_push_preview", push_preview)
        object.__setattr__(self, "_total_steps", total_steps)
        object.__setattr__(self, "streaming_supported", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_pipeline"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name == "streaming_supported":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_pipeline"), name, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        pipeline = object.__getattribute__(self, "_pipeline")
        total_steps = object.__getattribute__(self, "_total_steps")
        push_preview = object.__getattribute__(self, "_push_preview")
        callback_on_step_end, legacy_callback, _ = make_callbacks(
            push_preview,
            pipeline,
            total_steps,
            height=kwargs.get("height"),
            width=kwargs.get("width"),
        )
        try:
            parameters = inspect.signature(pipeline.__call__).parameters
        except (TypeError, ValueError):
            parameters = {}

        if "callback_on_step_end" in parameters and "callback_on_step_end" not in kwargs:
            kwargs["callback_on_step_end"] = callback_on_step_end
            if "callback_on_step_end_tensor_inputs" in parameters:
                kwargs.setdefault("callback_on_step_end_tensor_inputs", ["latents"])
            object.__setattr__(self, "streaming_supported", True)
        elif "callback" in parameters and "callback" not in kwargs:
            kwargs["callback"] = legacy_callback
            if "callback_steps" in parameters:
                kwargs.setdefault("callback_steps", 1)
            object.__setattr__(self, "streaming_supported", True)
        return pipeline(*args, **kwargs)


def call_generate_with_preview(
    generator: Any,
    generate_kwargs: Dict[str, Any],
    push_preview: Callable[[Image.Image, int], None],
    total_steps: int,
) -> Tuple[Any, bool]:
    pipeline_slot = find_pipeline_slot(generator)
    pipeline = pipeline_slot[2] if pipeline_slot else None
    try:
        parameters = inspect.signature(generator.generate).parameters
    except (TypeError, ValueError):
        parameters = {}

    callback_on_step_end, legacy_callback, generic_callback = make_callbacks(
        push_preview, pipeline, total_steps
    )
    direct_kwargs = dict(generate_kwargs)
    if "callback_on_step_end" in parameters:
        direct_kwargs["callback_on_step_end"] = callback_on_step_end
        if "callback_on_step_end_tensor_inputs" in parameters:
            direct_kwargs["callback_on_step_end_tensor_inputs"] = ["latents"]
        return generator.generate(**direct_kwargs), True
    if "callback" in parameters:
        direct_kwargs["callback"] = legacy_callback
        if "callback_steps" in parameters:
            direct_kwargs["callback_steps"] = 1
        return generator.generate(**direct_kwargs), True
    for callback_name in ("progress_callback", "preview_callback", "step_callback"):
        if callback_name in parameters:
            direct_kwargs[callback_name] = generic_callback
            return generator.generate(**direct_kwargs), True

    if pipeline_slot is not None:
        owner, attribute_name, original_pipeline = pipeline_slot
        proxy = StreamingPipelineProxy(original_pipeline, push_preview, total_steps)
        try:
            setattr(owner, attribute_name, proxy)
            result = generator.generate(**generate_kwargs)
            return result, bool(proxy.streaming_supported)
        finally:
            try:
                setattr(owner, attribute_name, original_pipeline)
            except (AttributeError, RuntimeError, TypeError):
                pass
    return generator.generate(**generate_kwargs), False


def generate_single_segment_stream(
    generator: Any,
    generate_kwargs: Dict[str, Any],
    total_steps: int,
) -> Generator[Tuple[Image.Image, int], None, Tuple[Image.Image, bool, int]]:
    preview_queue: queue.Queue[Tuple[Image.Image, int]] = queue.Queue(maxsize=2)
    result_box: Dict[str, Any] = {}
    error_box: Dict[str, BaseException] = {}

    def push_preview(image: Image.Image, step: int) -> None:
        frame = image.copy().convert("RGB")
        try:
            preview_queue.put_nowait((frame, step))
        except queue.Full:
            try:
                preview_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                preview_queue.put_nowait((frame, step))
            except queue.Full:
                pass

    def worker() -> None:
        try:
            with MODEL_LOCK:
                raw, streaming_supported = call_generate_with_preview(
                    generator,
                    generate_kwargs,
                    push_preview,
                    total_steps,
                )
            result_box["raw"] = raw
            result_box["streaming_supported"] = streaming_supported
        except BaseException as error:
            error_box["error"] = error

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    preview_count = 0
    while thread.is_alive() or not preview_queue.empty():
        try:
            image, step = preview_queue.get(timeout=0.12)
            preview_count += 1
            yield image, step
        except queue.Empty:
            continue

    thread.join()
    if "error" in error_box:
        raise error_box["error"]

    raw_result = result_box.get("raw")
    result_image = raw_result[0] if isinstance(raw_result, (tuple, list)) and raw_result else raw_result
    final_image = coerce_pil_image(result_image)
    if final_image is None:
        raise RuntimeError("模型未返回可识别的图像。")
    return final_image, bool(result_box.get("streaming_supported")), preview_count


def resize_to_height(image: Image.Image, target_height: int) -> Image.Image:
    image = image.convert("RGB")
    if image.height == target_height:
        return image
    target_width = max(1, round(image.width * target_height / image.height))
    return image.resize((target_width, target_height), Image.Resampling.LANCZOS)


def widen_image(image: Image.Image, factor: float = 1.15) -> Image.Image:
    """横向拉宽图片（保持高度不变，文字随像素同步变宽）。"""
    image = image.convert("RGB")
    width, height = image.size
    target_width = max(1, round(width * factor))
    return image.resize((target_width, height), Image.Resampling.LANCZOS)


def compose_seamless_scroll(
    completed_images: Sequence[Image.Image],
    background_mode: str = "纸白",
) -> Optional[Image.Image]:
    """Export a right-origin scroll with no visible seams.

    砚黑 mode treats the pale source paper as transparency and lifts dark ink
    into a restrained warm-metal tone, mirroring the browser's 砚黑 display
    instead of leaving white image rectangles on a dark canvas.
    """
    if not completed_images:
        return None

    ordered = [image.convert("RGB") for image in reversed(completed_images)]
    target_height = max(image.height for image in ordered)
    normalized = [resize_to_height(image, target_height) for image in ordered]
    overlap = 1 if len(normalized) > 1 else 0
    canvas_width = sum(image.width for image in normalized) - overlap * (len(normalized) - 1)

    if background_mode == "砚黑":
        background = (24, 21, 18)
        metal_ink = (214, 199, 166)
        canvas = Image.new("RGB", (max(1, canvas_width), target_height), background)
        x = 0
        for image in normalized:
            gray = ImageOps.grayscale(image)
            alpha = ImageOps.invert(gray).point(
                lambda value: 0 if value < 12 else min(255, int(value * 1.18))
            )
            ink_layer = Image.new("RGB", image.size, metal_ink)
            canvas.paste(ink_layer, (x, 0), alpha)
            x += image.width - overlap
        return canvas

    background = (250, 249, 246)
    canvas = Image.new("RGB", (max(1, canvas_width), target_height), background)
    x = 0
    for image in normalized:
        canvas.paste(image, (x, 0))
        x += image.width - overlap
    return canvas
