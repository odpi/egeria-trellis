#!/usr/bin/env python3
"""
Convert sentence-transformers model to ONNX format.

This script exports the embedding model to ONNX for improved performance
and cross-platform acceleration support.
"""

import argparse
from pathlib import Path
import torch
from sentence_transformers import SentenceTransformer
from loguru import logger
import sys


def convert_model_to_onnx(
    model_name: str,
    output_path: Path,
    opset_version: int = 14,
    optimize: bool = True
) -> bool:
    """
    Convert sentence-transformers model to ONNX format.
    
    Parameters
    ----------
    model_name : str
        Name of the sentence-transformers model
    output_path : Path
        Path to save the ONNX model
    opset_version : int
        ONNX opset version (default: 14)
    optimize : bool
        Whether to optimize the ONNX model
        
    Returns
    -------
    bool
        True if conversion successful
    """
    try:
        from advisor.embeddings import embedding_dimension  # TR-118: the current method name, old one as fallback

        logger.info(f"Loading model: {model_name}")
        model = SentenceTransformer(model_name, device="cpu")
        
        # Get model info
        embedding_dim = embedding_dimension(model)
        max_seq_length = model.max_seq_length
        
        logger.info(f"Model info:")
        logger.info(f"  Embedding dimension: {embedding_dim}")
        logger.info(f"  Max sequence length: {max_seq_length}")
        
        # Create dummy input for tracing
        dummy_input = {
            "input_ids": torch.randint(0, 1000, (1, max_seq_length)),
            "attention_mask": torch.ones(1, max_seq_length, dtype=torch.long)
        }
        
        logger.info("Converting to ONNX...")
        
        # Export to ONNX
        torch.onnx.export(
            model[0].auto_model,  # The transformer model
            (dummy_input["input_ids"], dummy_input["attention_mask"]),
            str(output_path),
            input_names=["input_ids", "attention_mask"],
            output_names=["last_hidden_state"],
            dynamic_axes={
                "input_ids": {0: "batch_size", 1: "sequence_length"},
                "attention_mask": {0: "batch_size", 1: "sequence_length"},
                "last_hidden_state": {0: "batch_size", 1: "sequence_length"}
            },
            opset_version=opset_version,
            do_constant_folding=True
        )
        
        logger.info(f"✓ Model exported to: {output_path}")
        
        # Optimize if requested
        if optimize:
            try:
                import onnx
                from onnxruntime.transformers import optimizer
                
                logger.info("Optimizing ONNX model...")
                
                # Load and optimize
                onnx_model = onnx.load(str(output_path))
                optimized_model = optimizer.optimize_model(
                    str(output_path),
                    model_type="bert",
                    num_heads=12,  # For MiniLM
                    hidden_size=384  # For MiniLM-L6
                )
                
                # Save optimized model
                optimized_path = output_path.with_suffix(".optimized.onnx")
                optimized_model.save_model_to_file(str(optimized_path))
                
                logger.info(f"✓ Optimized model saved to: {optimized_path}")
                
            except ImportError:
                logger.warning("onnxruntime.transformers not available, skipping optimization")
            except Exception as e:
                logger.warning(f"Optimization failed: {e}")
        
        # Verify the model
        logger.info("Verifying ONNX model...")
        import onnx
        onnx_model = onnx.load(str(output_path))
        onnx.checker.check_model(onnx_model)
        logger.info("✓ ONNX model is valid")
        
        return True
        
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Convert sentence-transformers model to ONNX"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="Model name or path"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/all-MiniLM-L6-v2.onnx"),
        help="Output path for ONNX model"
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=14,
        help="ONNX opset version"
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="Skip optimization step"
    )
    
    args = parser.parse_args()
    
    # Create output directory
    args.output.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert model
    success = convert_model_to_onnx(
        model_name=args.model,
        output_path=args.output,
        opset_version=args.opset,
        optimize=not args.no_optimize
    )
    
    if success:
        logger.info("✓ Conversion completed successfully")
        sys.exit(0)
    else:
        logger.error("✗ Conversion failed")
        sys.exit(1)


if __name__ == "__main__":
    main()