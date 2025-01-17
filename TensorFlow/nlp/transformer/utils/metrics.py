# coding=utf-8
# Copyright 2021 The Tensor2Tensor Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
###############################################################################
# Copyright (C) 2021 Habana Labs, Ltd. an Intel Company
###############################################################################
# Changes:
# - updated imports
# - added to_float util

"""Utils for metrics used in eval."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import six

from TensorFlow.nlp.transformer.layers import common_layers
from TensorFlow.nlp.transformer.layers import modalities
from TensorFlow.nlp.transformer.utils import bleu_hook
from TensorFlow.nlp.transformer.utils import contrib
from TensorFlow.nlp.transformer.utils import rouge
from TensorFlow.nlp.transformer.utils import sari_hook

import tensorflow.compat.v1 as tf
from tensorflow.python.util import tf_inspect as inspect


class Metrics(object):
  """Available evaluation metrics."""
  # Entries here should match the keys in METRICS_FNS below
  ACC = "accuracy"
  ACC_TOP5 = "accuracy_top5"
  ACC_PER_SEQ = "accuracy_per_sequence"
  ACC_MULTILABEL_MATCH3 = "accuracy_multilabel_match3"
  NEG_LOG_PERPLEXITY = "neg_log_perplexity"
  MASKED_NEG_LOG_PERPLEXITY = "masked_neg_log_perplexity"
  APPROX_BLEU = "approx_bleu_score"
  APPROX_SARI = "approx_sari_score"
  RMSE = "rmse"
  UNPADDED_MSE = "unpadded_mse"
  LOG_POISSON = "log_poisson"
  PEARSON = "pearson"
  R2 = "r_squared"
  ROUGE_2_F = "rouge_2_fscore"
  ROUGE_L_F = "rouge_L_fscore"
  EDIT_DISTANCE = "edit_distance"
  PREFIX_ACCURACY = "prefix_accuracy"
  WORD_ERROR_RATE = "word_error_rate"
  SET_PRECISION = "set_precision"
  SET_RECALL = "set_recall"
  SOFTMAX_CROSS_ENTROPY_ONE_HOT = "softmax_cross_entropy_one_hot"
  SIGMOID_ACCURACY_ONE_HOT = "sigmoid_accuracy_one_hot"
  SIGMOID_ACCURACY = "sigmoid_accuracy"
  SIGMOID_RECALL_ONE_HOT = "sigmoid_recall_one_hot"
  SIGMOID_PRECISION_ONE_HOT = "sigmoid_precision_one_hot"
  SIGMOID_CROSS_ENTROPY_ONE_HOT = "sigmoid_cross_entropy_one_hot"
  TWO_CLASS_ACCURACY = "two_class_accuracy"
  TWO_CLASS_LOG_LIKELIHOOD = "two_class_log_likelihood"
  ROC_AUC = "roc_auc"
  IMAGE_SUMMARY = "image_summary"
  DMOL_PERPLEXITY = "disc_mol_neg_log_perplexity"
  ABS_ERR = "mean_absolute_error"
  IMAGE_RMSE = "image_rmse"

def to_float(x):
  """Cast x to float; created because tf.to_float is deprecated."""
  return tf.cast(x, tf.float32)

def image_rmse(predictions, labels, weights_fn=common_layers.weights_all):
  """RMSE but will argmax if last dim is not 1."""
  if common_layers.shape_list(predictions)[-1] == 1:
    predictions = tf.squeeze(predictions, axis=[-1])
  else:
    predictions = tf.argmax(predictions, axis=-1)
  return padded_rmse(predictions, labels, weights_fn)


def padded_rmse(predictions, labels, weights_fn=common_layers.weights_all):
  predictions = to_float(predictions)
  labels = to_float(labels)
  predictions, labels = common_layers.pad_with_zeros(predictions, labels)
  weights = weights_fn(labels)
  error = tf.pow(predictions - labels, 2)
  error_sqrt = tf.sqrt(tf.reduce_mean(error * weights))
  return error_sqrt, tf.reduce_sum(weights)


def unpadded_mse(predictions, labels, weights_fn=common_layers.weights_all):
  predictions = to_float(predictions)
  labels = to_float(labels)
  weights = weights_fn(labels)
  error = tf.pow(predictions - labels, 2)
  mean_error = tf.reduce_mean(error * weights)
  return mean_error, tf.reduce_sum(weights)


def abs_error(predictions, labels, weights_fn=None):
  """Computes mean(abs(preds-target))."""
  del weights_fn  # Unused
  targets = tf.squeeze(labels, axis=[2, 3])
  batch_abs_error = tf.abs(predictions - targets)
  den = tf.ones(tf.shape(batch_abs_error), dtype=tf.float32)
  return (batch_abs_error, den)


def padded_log_poisson(predictions,
                       labels,
                       weights_fn=common_layers.weights_all):
  # Expects predictions to already be transformed into log space
  predictions, labels = common_layers.pad_with_zeros(predictions, labels)
  targets = labels
  weights = weights_fn(targets)

  lp_loss = tf.nn.log_poisson_loss(targets, predictions, compute_full_loss=True)
  return tf.reduce_sum(lp_loss * weights), tf.reduce_sum(weights)


def padded_variance_explained(predictions,
                              labels,
                              weights_fn=common_layers.weights_all):
  """Explained variance, also known as R^2."""
  predictions, labels = common_layers.pad_with_zeros(predictions, labels)
  targets = labels
  weights = weights_fn(targets)

  y_bar = tf.reduce_mean(weights * targets)
  tot_ss = tf.reduce_sum(weights * tf.pow(targets - y_bar, 2))
  res_ss = tf.reduce_sum(weights * tf.pow(targets - predictions, 2))
  r2 = 1. - res_ss / tot_ss
  return r2, tf.reduce_sum(weights)


def padded_accuracy_topk(predictions,
                         labels,
                         k,
                         weights_fn=common_layers.weights_nonzero):
  """Percentage of times that top-k predictions matches labels on non-0s."""
  with tf.variable_scope("padded_accuracy_topk", values=[predictions, labels]):
    padded_predictions, padded_labels = common_layers.pad_with_zeros(
        predictions, labels)
    weights = weights_fn(padded_labels)
    effective_k = tf.minimum(k,
                             common_layers.shape_list(padded_predictions)[-1])
    _, outputs = tf.nn.top_k(padded_predictions, k=effective_k)
    outputs = tf.to_int32(outputs)
    padded_labels = tf.to_int32(padded_labels)
    padded_labels = tf.expand_dims(padded_labels, axis=-1)
    padded_labels += tf.zeros_like(outputs)  # Pad to same shape.
    same = to_float(tf.equal(outputs, padded_labels))
    same_topk = tf.reduce_sum(same, axis=-1)
    return same_topk, weights


def padded_accuracy_top5(predictions,
                         labels,
                         weights_fn=common_layers.weights_nonzero):
  return padded_accuracy_topk(predictions, labels, 5, weights_fn)


def rounding_sequence_accuracy(predictions,
                               labels,
                               weights_fn=common_layers.weights_nonzero):
  """Sequence accuracy for L1/L2 losses: round down the predictions to ints."""
  outputs = tf.squeeze(tf.to_int32(predictions), axis=-1)
  weights = weights_fn(labels)
  labels = tf.to_int32(labels)
  not_correct = to_float(tf.not_equal(outputs, labels)) * weights
  axis = list(range(1, len(outputs.get_shape())))
  correct_seq = 1.0 - tf.minimum(1.0, tf.reduce_sum(not_correct, axis=axis))
  return correct_seq, tf.constant(1.0)


def two_class_accuracy(predictions, labels, weights_fn=None):
  """Accuracy for two class classification with 0/1 labels."""
  with tf.variable_scope("two_class_accuracy", values=[predictions, labels]):
    del weights_fn
    hard_predictions = tf.to_int32(tf.math.round(tf.squeeze(predictions)))
    int_labels = tf.to_int32(labels)
    _, accuracy = tf.metrics.accuracy(labels=int_labels,
                                      predictions=hard_predictions)
    return accuracy, tf.constant(1.0)


def two_class_log_likelihood(predictions, labels, weights_fn=None):
  """Log-likelihood for two class classification with 0/1 labels.

  Args:
    predictions: A float valued tensor of shape [`batch_size`].  Each
      component should be between 0 and 1.
    labels: An int valued tensor of shape [`batch_size`].  Each component
      should either be 0 or 1.
    weights_fn: unused.

  Returns:
    A pair, with the average log likelihood in the first component.
  """
  del weights_fn
  float_predictions = tf.cast(tf.squeeze(predictions), dtype=tf.float64)
  batch_probs = tf.stack([1. - float_predictions, float_predictions], axis=-1)
  int_labels = tf.cast(tf.squeeze(labels), dtype=tf.int32)
  onehot_targets = tf.cast(tf.one_hot(int_labels, 2), dtype=tf.float64)
  chosen_probs = tf.einsum(
      "ij,ij->i", batch_probs, onehot_targets, name="chosen_probs")
  avg_log_likelihood = tf.reduce_mean(tf.log(chosen_probs))
  return avg_log_likelihood, tf.constant(1.0)


def padded_sequence_accuracy(predictions,
                             labels,
                             weights_fn=common_layers.weights_nonzero):
  """Percentage of times that predictions matches labels everywhere (non-0)."""
  # If the last dimension is 1 then we're using L1/L2 loss.
  if common_layers.shape_list(predictions)[-1] == 1:
    return rounding_sequence_accuracy(
        predictions, labels, weights_fn=weights_fn)
  with tf.variable_scope(
      "padded_sequence_accuracy", values=[predictions, labels]):
    padded_predictions, padded_labels = common_layers.pad_with_zeros(
        predictions, labels)
    weights = weights_fn(padded_labels)

    # Flatten, keeping batch dim (and num_classes dim for predictions)
    # TPU argmax can only deal with a limited number of dimensions
    predictions_shape = common_layers.shape_list(padded_predictions)
    batch_size = predictions_shape[0]
    num_classes = predictions_shape[-1]
    flat_size = common_layers.list_product(
        common_layers.shape_list(padded_labels)[1:])
    padded_predictions = tf.reshape(
        padded_predictions,
        [batch_size, common_layers.list_product(predictions_shape[1:-1]),
         num_classes])
    padded_labels = tf.reshape(padded_labels, [batch_size, flat_size])
    weights = tf.reshape(weights, [batch_size, flat_size])

    outputs = tf.to_int32(tf.argmax(padded_predictions, axis=-1))
    padded_labels = tf.to_int32(padded_labels)
    not_correct = to_float(tf.not_equal(outputs, padded_labels)) * weights
    axis = list(range(1, len(outputs.get_shape())))
    correct_seq = 1.0 - tf.minimum(1.0, tf.reduce_sum(not_correct, axis=axis))
    return correct_seq, tf.constant(1.0)


def prefix_accuracy(predictions,
                    labels,
                    weights_fn=common_layers.weights_nonzero):
  """Average # of correct tokens at start of sequences, ignoring padding 0s.

  See section 4.3 of Learning to Transduce with Unbounded Memory,
  Grefenstette et al., 2015.

  Args:
    predictions: Tensor of shape [`batch_size`, `length`, 1, `num_classes`] and
        type tf.float32 representing the logits, 0-padded.
    labels: Tensor of shape [`batch_size`, `length`, 1, 1] and type tf.int32
        representing the labels of same length as logits and 0-padded.
    weights_fn: ignored. The weights returned are the total length of the ground
        truth labels, excluding 0-paddings.

  Returns:
    (prefix accuracy, 1.0)

  Raises:
    ValueError: if weights_fn is not common_layers.weights_nonzero.
  """
  if weights_fn is not common_layers.weights_nonzero:
    raise ValueError("Only weights_nonzero can be used for this metric.")

  predictions = tf.to_int32(tf.squeeze(tf.argmax(predictions, axis=-1), axis=2))
  labels = tf.squeeze(labels, axis=(2, 3))
  seq_len = tf.reduce_sum(
      tf.cast(tf.not_equal(labels, tf.constant(0)), dtype=tf.float32), axis=1)
  matching_elements = tf.equal(labels, predictions)
  prefix_len = tf.reduce_sum(
      tf.cumprod(tf.cast(matching_elements, tf.float32), axis=1), axis=1)
  return tf.reduce_mean(prefix_len / seq_len), tf.constant(1.0)


def sequence_edit_distance(predictions,
                           labels,
                           weights_fn=common_layers.weights_nonzero):
  """Average edit distance, ignoring padding 0s.

  The score returned is the edit distance divided by the total length of
  reference truth and the weight returned is the total length of the truth.

  Args:
    predictions: Tensor of shape [`batch_size`, `length`, 1, `num_classes`] and
        type tf.float32 representing the logits, 0-padded.
    labels: Tensor of shape [`batch_size`, `length`, 1, 1] and type tf.int32
        representing the labels of same length as logits and 0-padded.
    weights_fn: ignored. The weights returned are the total length of the ground
        truth labels, excluding 0-paddings.

  Returns:
    (edit distance / reference length, reference length)

  Raises:
    ValueError: if weights_fn is not common_layers.weights_nonzero.
  """
  if weights_fn is not common_layers.weights_nonzero:
    raise ValueError("Only weights_nonzero can be used for this metric.")

  with tf.variable_scope("edit_distance", values=[predictions, labels]):
    # Transform logits into sequence classes by taking max at every step.
    predictions = tf.to_int32(
        tf.squeeze(tf.argmax(predictions, axis=-1), axis=(2, 3)))
    nonzero_idx = tf.where(tf.not_equal(predictions, 0))
    sparse_outputs = tf.SparseTensor(nonzero_idx,
                                     tf.gather_nd(predictions, nonzero_idx),
                                     tf.shape(predictions, out_type=tf.int64))
    labels = tf.squeeze(labels, axis=(2, 3))
    nonzero_idx = tf.where(tf.not_equal(labels, 0))
    label_sparse_outputs = tf.SparseTensor(nonzero_idx,
                                           tf.gather_nd(labels, nonzero_idx),
                                           tf.shape(labels, out_type=tf.int64))
    distance = tf.reduce_sum(
        tf.edit_distance(sparse_outputs, label_sparse_outputs, normalize=False))
    reference_length = to_float(common_layers.shape_list(nonzero_idx)[0])
    return distance / reference_length, reference_length


def padded_neg_log_perplexity(predictions,
                              labels,
                              weights_fn=common_layers.weights_nonzero):
  """Average log-perplexity exluding padding 0s. No smoothing."""
  num, den = common_layers.padded_cross_entropy(
      predictions, labels, 0.0, weights_fn=weights_fn, reduce_sum=False)
  return (-num, den)


def padded_neg_log_perplexity_with_masking(
    predictions,
    labels,
    features,
    weights_fn=None):
  """Average log-perplexity with custom targets_mask."""
  del weights_fn
  if "targets_mask" not in features:
    raise ValueError("masked_neg_log_perplexity requires targets_mask feature")

  # Features are 4 dimensional, so we need to reshape the targets_mask to match
  # the shape of the labels. A lot of models rely on these features being 4D,
  # so it's best to update the shape of the mask.
  extended_targets_mask_shape = common_layers.shape_list(
      features["targets_mask"])
  extended_targets_mask_shape.extend([1, 1])
  features["targets_mask"] = tf.reshape(features["targets_mask"],
                                        shape=extended_targets_mask_shape)

  mask_fn = lambda labels: features["targets_mask"]
  return padded_neg_log_perplexity(predictions, labels, mask_fn)


def dmol_neg_log_perplexity(predictions,
                            labels,
                            weights_fn=None):
  """Average log-perplexity excluding padding 0s. No smoothing."""
  del weights_fn  # Unused
  num, den = common_layers.dml_loss(
      predictions, labels, reduce_sum=False)
  return (-num, den)


def rounding_accuracy(predictions,
                      labels,
                      weights_fn=common_layers.weights_nonzero):
  """Rounding accuracy for L1/L2 losses: round down the predictions to ints."""
  outputs = tf.squeeze(tf.to_int32(predictions))
  labels = tf.squeeze(labels)
  weights = weights_fn(labels)
  labels = tf.to_int32(labels)
  return to_float(tf.equal(outputs, labels)), weights


def padded_accuracy(predictions,
                    labels,
                    weights_fn=common_layers.weights_nonzero):
  """Percentage of times that predictions matches labels on non-0s."""
  # If the last dimension is 1 then we're using L1/L2 loss.
  if common_layers.shape_list(predictions)[-1] == 1:
    return rounding_accuracy(predictions, labels, weights_fn=weights_fn)
  with tf.variable_scope("padded_accuracy", values=[predictions, labels]):
    padded_predictions, padded_labels = common_layers.pad_with_zeros(
        predictions, labels)
    weights = weights_fn(padded_labels)
    outputs = tf.to_int32(tf.argmax(padded_predictions, axis=-1))
    padded_labels = tf.to_int32(padded_labels)
    return to_float(tf.equal(outputs, padded_labels)), weights


def multilabel_accuracy_matchk(predictions,
                               labels,
                               k,
                               weights_fn=common_layers.weights_nonzero):
  """Used to evaluate the VQA accuracy.

  Let n be the times that predictions appear in labels, then final score
  is min(n/k, 1).
  Refer to https://arxiv.org/pdf/1505.00468.pdf.

  Args:
    predictions: A tensor with shape [batch_size, 1, 1, 1, vocab_size].
    labels: A tensor with shape [batch_size, length, 1, 1].
    k: A tensor constant.
    weights_fn: weight function.
  Returns:
    scores: min(n/k, 1).
    weights: returns all ones.

  """
  predictions = tf.to_int32(tf.argmax(predictions, axis=-1))
  scores = to_float(tf.equal(predictions, labels))
  # those label == 0 do not count
  weights = weights_fn(labels)
  scores *= weights
  scores = tf.reduce_sum(scores, axis=[1, 2, 3])
  scores = tf.minimum(scores / to_float(k), 1)
  # every sample count
  weights = tf.ones(tf.shape(scores), dtype=tf.float32)

  return scores, weights


def multilabel_accuracy_match3(predictions, labels,
                               weights_fn=common_layers.weights_nonzero):
  return multilabel_accuracy_matchk(predictions, labels, 3, weights_fn)


def set_precision(predictions, labels,
                  weights_fn=common_layers.weights_nonzero):
  """Precision of set predictions.

  Args:
    predictions : A Tensor of scores of shape [batch, nlabels].
    labels: A Tensor of int32s giving true set elements,
      of shape [batch, seq_length].
    weights_fn: A function to weight the elements.

  Returns:
    hits: A Tensor of shape [batch, nlabels].
    weights: A Tensor of shape [batch, nlabels].
  """
  with tf.variable_scope("set_precision", values=[predictions, labels]):
    labels = tf.squeeze(labels, [2, 3])
    weights = weights_fn(labels)
    labels = tf.one_hot(labels, predictions.shape[-1])
    labels = tf.reduce_max(labels, axis=1)
    labels = tf.cast(labels, tf.bool)
    return to_float(tf.equal(labels, predictions)), weights


def set_recall(predictions, labels, weights_fn=common_layers.weights_nonzero):
  """Recall of set predictions.

  Args:
    predictions : A Tensor of scores of shape [batch, nlabels].
    labels: A Tensor of int32s giving true set elements,
      of shape [batch, seq_length].
    weights_fn: A function to weight the elements.

  Returns:
    hits: A Tensor of shape [batch, nlabels].
    weights: A Tensor of shape [batch, nlabels].
  """
  with tf.variable_scope("set_recall", values=[predictions, labels]):
    labels = tf.squeeze(labels, [2, 3])
    weights = weights_fn(labels)
    labels = tf.one_hot(labels, predictions.shape[-1])
    labels = tf.reduce_max(labels, axis=1)
    labels = tf.cast(labels, tf.bool)
    return to_float(tf.equal(labels, predictions)), weights


def image_summary(predictions, targets, hparams):
  """Reshapes predictions and passes it to tensorboard.

  Args:
    predictions : The predicted image (logits).
    targets : The ground truth.
    hparams: model hparams.

  Returns:
    summary_proto: containing the summary images.
    weights: A Tensor of zeros of the same shape as predictions.
  """
  del hparams
  results = tf.cast(tf.argmax(predictions, axis=-1), tf.uint8)
  gold = tf.cast(targets, tf.uint8)
  summary1 = tf.summary.image("prediction", results, max_outputs=2)
  summary2 = tf.summary.image("data", gold, max_outputs=2)
  summary = tf.summary.merge([summary1, summary2])
  return summary, tf.zeros_like(predictions)


def softmax_cross_entropy_one_hot(logits, labels, weights_fn=None):
  """Calculate softmax cross entropy given one-hot labels and logits.

  Args:
    logits: Tensor of size [batch-size, o=1, p=1, num-classes]
    labels: Tensor of size [batch-size, o=1, p=1, num-classes]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    cross-entropy (scalar), weights
  """
  with tf.variable_scope("softmax_cross_entropy_one_hot",
                         values=[logits, labels]):
    del weights_fn
    cross_entropy = tf.losses.softmax_cross_entropy(
        onehot_labels=labels, logits=logits)
    return cross_entropy, tf.constant(1.0)


def sigmoid_accuracy_one_hot(logits, labels, weights_fn=None):
  """Calculate accuracy for a set, given one-hot labels and logits.

  Args:
    logits: Tensor of size [batch-size, o=1, p=1, num-classes]
    labels: Tensor of size [batch-size, o=1, p=1, num-classes]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    accuracy (scalar), weights
  """
  with tf.variable_scope("sigmoid_accuracy_one_hot", values=[logits, labels]):
    del weights_fn
    predictions = tf.nn.sigmoid(logits)
    labels = tf.argmax(labels, -1)
    predictions = tf.argmax(predictions, -1)
    _, accuracy = tf.metrics.accuracy(labels=labels, predictions=predictions)
    return accuracy, tf.constant(1.0)


def sigmoid_accuracy(logits, labels, weights_fn=None):
  """Calculate accuracy for a set, given integer labels and logits.

  Args:
    logits: Tensor of size [batch-size, o=1, p=1, num-classes]
    labels: Tensor of size [batch-size, o=1, p=1]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    accuracy (scalar), weights
  """
  with tf.variable_scope("sigmoid_accuracy", values=[logits, labels]):
    del weights_fn
    predictions = tf.nn.sigmoid(logits)
    predictions = tf.argmax(predictions, -1)
    _, accuracy = tf.metrics.accuracy(labels=labels, predictions=predictions)
    return accuracy, tf.constant(1.0)


def sigmoid_precision_one_hot(logits, labels, weights_fn=None):
  """Calculate precision for a set, given one-hot labels and logits.

  Predictions are converted to one-hot,
  as predictions[example][arg-max(example)] = 1

  Args:
    logits: Tensor of size [batch-size, o=1, p=1, num-classes]
    labels: Tensor of size [batch-size, o=1, p=1, num-classes]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    precision (scalar), weights
  """
  with tf.variable_scope("sigmoid_precision_one_hot", values=[logits, labels]):
    del weights_fn
    num_classes = logits.shape[-1]
    predictions = tf.nn.sigmoid(logits)
    predictions = tf.argmax(predictions, -1)
    predictions = tf.one_hot(predictions, num_classes)
    _, precision = tf.metrics.precision(labels=labels, predictions=predictions)
    return precision, tf.constant(1.0)


def sigmoid_recall_one_hot(logits, labels, weights_fn=None):
  """Calculate recall for a set, given one-hot labels and logits.

  Predictions are converted to one-hot,
  as predictions[example][arg-max(example)] = 1

  Args:
    logits: Tensor of size [batch-size, o=1, p=1, num-classes]
    labels: Tensor of size [batch-size, o=1, p=1, num-classes]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    recall (scalar), weights
  """
  with tf.variable_scope("sigmoid_recall_one_hot", values=[logits, labels]):
    del weights_fn
    num_classes = logits.shape[-1]
    predictions = tf.nn.sigmoid(logits)
    predictions = tf.argmax(predictions, -1)
    predictions = tf.one_hot(predictions, num_classes)
    _, recall = tf.metrics.recall(labels=labels, predictions=predictions)
    return recall, tf.constant(1.0)


def sigmoid_cross_entropy_one_hot(logits, labels, weights_fn=None):
  """Calculate sigmoid cross entropy for one-hot lanels and logits.

  Args:
    logits: Tensor of size [batch-size, o=1, p=1, num-classes]
    labels: Tensor of size [batch-size, o=1, p=1, num-classes]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    cross_entropy (scalar), weights
  """
  with tf.variable_scope("sigmoid_cross_entropy_one_hot",
                         values=[logits, labels]):
    del weights_fn
    cross_entropy = tf.losses.sigmoid_cross_entropy(
        multi_class_labels=labels, logits=logits)
    return cross_entropy, tf.constant(1.0)


def roc_auc(logits, labels, weights_fn=None):
  """Calculate ROC AUC.

  Requires binary classes.

  Args:
    logits: Tensor of size [batch_size, 1, 1, num_classes]
    labels: Tensor of size [batch_size, 1, 1, num_classes]
    weights_fn: Function that takes in labels and weighs examples (unused)
  Returns:
    ROC AUC (scalar), weights
  """
  del weights_fn
  with tf.variable_scope("roc_auc", values=[logits, labels]):
    predictions = tf.argmax(logits, axis=-1)
    _, auc = tf.metrics.auc(labels, predictions, curve="ROC")
    return auc, tf.constant(1.0)


def create_evaluation_metrics(problems, model_hparams):
  """Creates the evaluation metrics for the model.

  Args:
    problems: List of Problem instances.
    model_hparams: a set of hparams.

  Returns:
    dict<metric name, metric function>. The metric functions have signature
    (Tensor predictions, features) -> (metric Tensor, update op), where features
    is a dict with keys {targets}.

  Raises:
    ValueError: if the metrics specified by a problem are not recognized (i.e.
      are not defined in the Metrics enum.
  """
  def reduce_dimensions(predictions, labels):
    """Reduce dimensions for high-dimensional predictions and labels."""
    # We will treat first dimensions as batch. One example are video frames.
    if len(predictions.get_shape()) > 5:
      predictions_shape = common_layers.shape_list(predictions)
      predictions = tf.reshape(
          predictions, [predictions_shape[0], predictions_shape[1], -1,
                        predictions_shape[-1]])
      labels_shape = common_layers.shape_list(labels)
      labels = tf.reshape(
          labels, [labels_shape[0], labels_shape[1], -1])
    return predictions, labels

  def make_problem_specific_metric_fn(metric_fn, weights_fn):
    """Create a metric fn."""

    def problem_metric_fn(predictions, features, labels):
      """Metric fn."""
      # Send along the entire features dict if the metric fn has the kwarg
      # "features".
      kwargs = {}
      args, _, keywords, _ = inspect.getargspec(metric_fn)
      if ("features" in args) or keywords:
        kwargs["features"] = features

      predictions, labels = reduce_dimensions(predictions, labels)

      scores, weights = metric_fn(predictions, labels,
                                  weights_fn=weights_fn, **kwargs)
      return tf.metrics.mean(scores, weights)

    return problem_metric_fn

  def make_image_wrapped_metric_fn(metric_fn):
    """Metric fn without tf.metrics.mean."""

    def image_wrapped_metric_fn(predictions,
                                features,
                                labels,
                                weights_fn=common_layers.weights_all):
      del weights_fn
      del features
      predictions, labels = reduce_dimensions(predictions, labels)
      return metric_fn(predictions, labels, model_hparams)

    return image_wrapped_metric_fn

  def weights_fn_for_mp(problem_task_id):
    return lambda x: common_layers.weights_multi_problem(x, problem_task_id)

  eval_metrics = {}
  for problem_instance in problems:
    problem_name = problem_instance.name
    if problem_instance.was_reversed:
      problem_name += "_rev"
    metrics = problem_instance.eval_metric_fns(model_hparams)
    if hasattr(model_hparams.problem, "task_list"):
      metrics = model_hparams.problem.eval_metric_fns(model_hparams)

    tm = problem_instance.get_hparams(model_hparams).modality["targets"]
    if not isinstance(tm, dict):
      tm = {"targets": tm}

    for target_name, modality in six.iteritems(tm):
      weights_fn = model_hparams.weights_fn.get(
          "targets",
          modalities.get_weights_fn(modality))
      if hasattr(model_hparams.problem, "task_list"):
        ptid = problem_instance.task_id  # pylint: disable=cell-var-from-loop
        weights_fn = weights_fn_for_mp(ptid)

      for metric, metric_fn in six.iteritems(metrics):
        overload_eval_metric_name = getattr(
            model_hparams, "overload_eval_metric_name", None)
        if len(problems) == 1 and overload_eval_metric_name:
          metric_name = "metrics-%s/%s/%s" % (
              overload_eval_metric_name, target_name, metric)
        else:
          metric_name = "metrics-%s/%s/%s" % (problem_name, target_name, metric)
        if metric == Metrics.IMAGE_SUMMARY:
          eval_metrics[metric_name] = make_image_wrapped_metric_fn(metric_fn)
        else:
          eval_metrics[metric_name] = make_problem_specific_metric_fn(
              metric_fn, weights_fn)

  return eval_metrics


def create_eager_metrics_for_problem(problem, model_hparams):
  """See create_eager_metrics."""
  metric_fns = problem.eval_metric_fns(model_hparams)
  problem_hparams = problem.get_hparams(model_hparams)
  target_modality = problem_hparams.modality["targets"]
  weights_fn = model_hparams.weights_fn.get(
      "targets",
      modalities.get_weights_fn(target_modality))
  return create_eager_metrics_internal(metric_fns, weights_fn=weights_fn)


def create_eager_metrics(metric_names, weights_fn=common_layers.weights_all):
  """Create metrics accumulators and averager for Eager mode.

  Args:
    metric_names: list<str> from Metrics enum
    weights_fn: function that takes labels and returns a weights mask. Defaults
      to weights of all 1, i.e. common_layers.weights_all. Use
      common_layers.weights_nonzero if labels have 0-padding.

  Returns:
    (accum_fn(predictions, targets) => None,
     result_fn() => dict<str metric_name, float avg_val>
  """
  metric_fns = dict(
      [(name, METRICS_FNS[name]) for name in metric_names])
  return create_eager_metrics_internal(metric_fns, weights_fn)


def create_eager_metrics_internal(metric_fns,
                                  weights_fn=common_layers.weights_all):
  """Create metrics accumulators and averager for Eager mode.

  Args:
    metric_fns: dict<metric name, metric function>
    weights_fn: function that takes labels and returns a weights mask. Defaults
      to weights of all 1, i.e. common_layers.weights_all. Use
      common_layers.weights_nonzero if labels have 0-padding.

  Returns:
    (accum_fn(predictions, targets) => None,
     result_fn() => dict<str metric_name, float avg_val>
  """

  from tensorflow.contrib.eager.python import tfe  # pylint: disable=g-import-not-at-top

  tfe_metrics = {}

  for name in metric_fns:
    tfe_metrics[name] = tfe.metrics.Mean(name=name)

  def metric_accum(predictions, targets):
    for name, metric_fn in metric_fns.items():
      val, weight = metric_fn(predictions, targets,
                              weights_fn=weights_fn)
      tfe_metrics[name](np.squeeze(val), np.squeeze(weight))

  def metric_means():
    avgs = {}
    for name in metric_fns:
      avgs[name] = tfe_metrics[name].result().numpy()
    return avgs

  return metric_accum, metric_means


def word_error_rate(raw_predictions,
                    labels,
                    lookup=None,
                    weights_fn=common_layers.weights_nonzero):
  """Calculate word error rate.

  Args:
    raw_predictions: The raw predictions.
    labels: The actual labels.
    lookup: A tf.constant mapping indices to output tokens.
    weights_fn: Weighting function.

  Returns:
    The word error rate.
  """

  def from_tokens(raw, lookup_):
    gathered = tf.gather(lookup_, tf.cast(raw, tf.int32))
    joined = tf.regex_replace(tf.reduce_join(gathered, axis=1), b"<EOS>.*", b"")
    cleaned = tf.regex_replace(joined, b"_", b" ")
    tokens = tf.string_split(cleaned, " ")
    return tokens

  def from_characters(raw, lookup_):
    """Convert ascii+2 encoded codes to string-tokens."""
    corrected = tf.bitcast(
        tf.clip_by_value(tf.subtract(raw, 2), 0, 255), tf.uint8)

    gathered = tf.gather(lookup_, tf.cast(corrected, tf.int32))[:, :, 0]
    joined = tf.reduce_join(gathered, axis=1)
    cleaned = tf.regex_replace(joined, b"\0", b"")
    tokens = tf.string_split(cleaned, " ")
    return tokens

  if lookup is None:
    lookup = tf.constant([chr(i) for i in range(256)])
    convert_fn = from_characters
  else:
    convert_fn = from_tokens

  if weights_fn is not common_layers.weights_nonzero:
    raise ValueError("Only weights_nonzero can be used for this metric.")

  with tf.variable_scope("word_error_rate", values=[raw_predictions, labels]):

    raw_predictions = tf.squeeze(
        tf.argmax(raw_predictions, axis=-1), axis=(2, 3))
    labels = tf.squeeze(labels, axis=(2, 3))

    reference = convert_fn(labels, lookup)
    predictions = convert_fn(raw_predictions, lookup)

    distance = tf.reduce_sum(
        tf.edit_distance(predictions, reference, normalize=False))
    reference_length = tf.cast(
        tf.size(reference.values, out_type=tf.int32), dtype=tf.float32)

    return distance / reference_length, reference_length


def pearson_correlation_coefficient(predictions, labels, weights_fn=None):
  """Calculate pearson correlation coefficient.

  Args:
    predictions: The raw predictions.
    labels: The actual labels.
    weights_fn: Weighting function.

  Returns:
    The pearson correlation coefficient.
  """
  del weights_fn
  _, pearson = contrib.metrics().streaming_pearson_correlation(
      predictions, labels)
  return pearson, tf.constant(1.0)

# Metrics are functions that take predictions and labels and return
# a tensor of metrics and a tensor of weights.
# If the function has "features" as an argument, it will receive the whole
# features dict as well.
# The results are passed to tf.metrics.mean to accumulate properly.
METRICS_FNS = {
    Metrics.ACC: padded_accuracy,
    Metrics.ACC_TOP5: padded_accuracy_top5,
    Metrics.ACC_PER_SEQ: padded_sequence_accuracy,
    Metrics.ACC_MULTILABEL_MATCH3: multilabel_accuracy_match3,
    Metrics.NEG_LOG_PERPLEXITY: padded_neg_log_perplexity,
    Metrics.MASKED_NEG_LOG_PERPLEXITY: padded_neg_log_perplexity_with_masking,
    Metrics.APPROX_BLEU: bleu_hook.bleu_score,
    Metrics.APPROX_SARI: sari_hook.sari_score,
    Metrics.RMSE: padded_rmse,
    Metrics.UNPADDED_MSE: unpadded_mse,
    Metrics.LOG_POISSON: padded_log_poisson,
    Metrics.PEARSON: pearson_correlation_coefficient,
    Metrics.R2: padded_variance_explained,
    Metrics.ROUGE_2_F: rouge.rouge_2_fscore,
    Metrics.ROUGE_L_F: rouge.rouge_l_fscore,
    Metrics.EDIT_DISTANCE: sequence_edit_distance,
    Metrics.SOFTMAX_CROSS_ENTROPY_ONE_HOT: softmax_cross_entropy_one_hot,
    Metrics.SIGMOID_ACCURACY: sigmoid_accuracy,
    Metrics.SIGMOID_ACCURACY_ONE_HOT: sigmoid_accuracy_one_hot,
    Metrics.SIGMOID_RECALL_ONE_HOT: sigmoid_recall_one_hot,
    Metrics.SIGMOID_PRECISION_ONE_HOT: sigmoid_precision_one_hot,
    Metrics.SIGMOID_CROSS_ENTROPY_ONE_HOT: sigmoid_cross_entropy_one_hot,
    Metrics.SET_PRECISION: set_precision,
    Metrics.SET_RECALL: set_recall,
    Metrics.TWO_CLASS_ACCURACY: two_class_accuracy,
    Metrics.TWO_CLASS_LOG_LIKELIHOOD: two_class_log_likelihood,
    Metrics.ROC_AUC: roc_auc,
    Metrics.IMAGE_SUMMARY: image_summary,
    Metrics.DMOL_PERPLEXITY: dmol_neg_log_perplexity,
    Metrics.ABS_ERR: abs_error,
    Metrics.IMAGE_RMSE: image_rmse,
    Metrics.WORD_ERROR_RATE: word_error_rate,
}
