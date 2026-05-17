"""Corpus catalogue: 50 Wikipedia articles + 3 GitHub READMEs.

This module is the single source of truth for the experiment corpus.
Both download_corpus.py and the cycle runners consume it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WikiArticle:
    slug: str  # used as filename and doc_id
    title: str  # canonical Wikipedia title (English)
    cluster: str


@dataclass(frozen=True)
class GitHubReadme:
    slug: str
    repo: str  # "owner/name"
    branch: str = "HEAD"


WIKI_ARTICLES: list[WikiArticle] = [
    # Cluster A — Classical foundations
    WikiArticle("artificial_intelligence", "Artificial intelligence", "A"),
    WikiArticle("machine_learning", "Machine learning", "A"),
    WikiArticle("supervised_learning", "Supervised learning", "A"),
    WikiArticle("unsupervised_learning", "Unsupervised learning", "A"),
    WikiArticle("reinforcement_learning", "Reinforcement learning", "A"),
    WikiArticle("overfitting", "Overfitting", "A"),
    WikiArticle("bias_variance_tradeoff", "Bias–variance tradeoff", "A"),
    WikiArticle("no_free_lunch_theorem", "No free lunch theorem", "A"),
    # Cluster B — Neural network fundamentals
    WikiArticle("neural_network", "Neural network (machine learning)", "B"),
    WikiArticle("perceptron", "Perceptron", "B"),
    WikiArticle("multilayer_perceptron", "Multilayer perceptron", "B"),
    WikiArticle("activation_function", "Activation function", "B"),
    WikiArticle("backpropagation", "Backpropagation", "B"),
    WikiArticle("gradient_descent", "Gradient descent", "B"),
    WikiArticle("stochastic_gradient_descent", "Stochastic gradient descent", "B"),
    WikiArticle("vanishing_gradient_problem", "Vanishing gradient problem", "B"),
    WikiArticle("batch_normalization", "Batch normalization", "B"),
    WikiArticle("dropout_neural_networks", "Dropout (neural networks)", "B"),
    # Cluster C — Architectures
    WikiArticle("convolutional_neural_network", "Convolutional neural network", "C"),
    WikiArticle("recurrent_neural_network", "Recurrent neural network", "C"),
    WikiArticle("long_short_term_memory", "Long short-term memory", "C"),
    WikiArticle("transformer", "Transformer (deep learning architecture)", "C"),
    WikiArticle("attention", "Attention (machine learning)", "C"),
    WikiArticle("encoder_decoder", "Encoder–decoder model", "C"),
    WikiArticle("autoencoder", "Autoencoder", "C"),
    WikiArticle("gan", "Generative adversarial network", "C"),
    WikiArticle("diffusion_model", "Diffusion model", "C"),
    WikiArticle("mixture_of_experts", "Mixture of experts", "C"),
    # Cluster D — Modern learning
    WikiArticle("transfer_learning", "Transfer learning", "D"),
    WikiArticle("few_shot_learning", "Few-shot learning (natural language processing)", "D"),
    WikiArticle("self_supervised_learning", "Self-supervised learning", "D"),
    WikiArticle("contrastive_learning", "Contrastive learning", "D"),
    WikiArticle("meta_learning", "Meta-learning (computer science)", "D"),
    WikiArticle("curriculum_learning", "Curriculum learning", "D"),
    WikiArticle("knowledge_distillation", "Knowledge distillation", "D"),
    WikiArticle("active_learning", "Active learning (machine learning)", "D"),
    # Cluster E — Evaluation / alignment
    WikiArticle("loss_function", "Loss function", "E"),
    WikiArticle("cross_entropy", "Cross-entropy", "E"),
    WikiArticle("rlhf", "Reinforcement learning from human feedback", "E"),
    WikiArticle("ai_alignment", "AI alignment", "E"),
    WikiArticle("hallucination_ai", "Hallucination (artificial intelligence)", "E"),
    WikiArticle("mechanistic_interpretability", "Mechanistic interpretability", "E"),
    # Cluster F — Applications / tooling
    WikiArticle("large_language_model", "Large language model", "F"),
    WikiArticle("computer_vision", "Computer vision", "F"),
    WikiArticle("natural_language_processing", "Natural language processing", "F"),
    WikiArticle("word_embedding", "Word embedding", "F"),
    WikiArticle("tokenization", "Lexical analysis", "F"),
    WikiArticle("embeddings", "Sentence embedding", "F"),
    WikiArticle("bert", "BERT (language model)", "F"),
    WikiArticle("gpt", "Generative pre-trained transformer", "F"),
]

GITHUB_READMES: list[GitHubReadme] = [
    GitHubReadme("huggingface_transformers", "huggingface/transformers"),
    GitHubReadme("pytorch", "pytorch/pytorch"),
    GitHubReadme("langchain", "langchain-ai/langchain"),
]


def first_n_wiki(n: int) -> list[WikiArticle]:
    """Return the first ``n`` Wikipedia articles (used by the mini-version)."""
    return WIKI_ARTICLES[:n]
