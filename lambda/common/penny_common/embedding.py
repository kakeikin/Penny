"""Titan Text Embeddings V2 — shared by IndexLambda (documents) and AdvisorLambda (queries)."""
import json

EMBED_MODEL_ID = 'amazon.titan-embed-text-v2:0'


def embed_text(bedrock, text: str, dimensions: int) -> tuple:
    """Return (embedding, input_token_count). Raises ValueError on a wrong-sized vector."""
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=json.dumps({'inputText': text, 'dimensions': dimensions, 'normalize': True}),
    )
    out = json.loads(resp['body'].read())
    embedding = out['embedding']
    if len(embedding) != dimensions:
        raise ValueError(f'expected {dimensions}-dim embedding, got {len(embedding)}')
    return embedding, out.get('inputTextTokenCount', 0)
