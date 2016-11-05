# tests: mypy

from typing import Any, Dict, List, Tuple
import os
import numpy as np
import tensorflow as tf
from termcolor import colored

from neuralmonkey.logging import log, log_print
from neuralmonkey.dataset import Dataset
from neuralmonkey.tf_manager import TensorFlowManager
from neuralmonkey.runners.base_runner import BaseRunner, ExecutionResult

# pylint: disable=invalid-name
Evaluation = Dict[str, float]


def training_loop(tf_manager: TensorFlowManager,
                  epochs: int,
                  trainer,
                  batch_size: int,
                  train_dataset: Dataset,
                  val_dataset: Dataset,
                  log_directory: str,
                  evaluators: List[Tuple[str, Any]],
                  runners: List[Any],
                  test_datasets=None,
                  save_n_best_vars=1,
                  link_best_vars="/tmp/variables.data.best",
                  vars_prefix="/tmp/variables.data",
                  logging_period=20,
                  validation_period=500,
                  postprocess=None,
                  minimize_metric=False):

    # TODO finish the list
    """
    Performs the training loop for given graph and data.

    Args:
        tf_manager: TensorFlowManager with initialized sessions.
        epochs: Number of epochs for which the algoritm will learn.
        trainer: The trainer object containg the TensorFlow code for computing
            the loss and optimization operation.
        train_dataset:
        val_dataset:
        postprocess: Function that takes the output sentence as produced by the
            decoder and transforms into tokenized sentence.
        log_directory: Directory where the TensordBoard log will be generated.
            If None, nothing will be done.
        evaluators: List of evaluators. The last evaluator
            is used as the main. Each function accepts list of decoded sequences
            and list of reference sequences and returns a float.

    """
    all_coders = encoders + [decoder]

    main_metric = "{}/{}".format(evaluators[-1][0], evaluators[-1][1].name)
    step = 0
    seen_instances = 0

    if save_n_best_vars < 1:
        raise Exception('save_n_best_vars must be greater than zero')

    if save_n_best_vars == 1:
        variables_files = [vars_prefix]
    elif save_n_best_vars > 1:
        variables_files = ['{}.{}'.format(vars_prefix, i)
                           for i in range(save_n_best_vars)]

    if minimize_metric:
        saved_scores = [np.inf for _ in range(save_n_best_vars)]
        best_score = np.inf
    else:
        saved_scores = [-np.inf for _ in range(save_n_best_vars)]
        best_score = -np.inf

    tf_manager.save(variables_files[0])

    if os.path.islink(link_best_vars):
        # if overwriting output dir
        os.unlink(link_best_vars)
    os.symlink(os.path.basename(variables_files[0]), link_best_vars)

    if log_directory:
        log("Initializing TensorBoard summary writer.")
        tb_writer = tf.train.SummaryWriter(log_directory,
                                           tf_manager.sessions[0].graph)
        log("TesorBoard writer initialized.")

    best_score_epoch = 0
    best_score_batch_no = 0

    log("Starting training")
    try:
        for i in range(epochs):
            log_print("")
            log("Epoch {} starts".format(i + 1), color='red')

            train_dataset.shuffle()
            train_batched_datasets = train_dataset.batch_dataset(batch_size)

            for batch_n, batch_dataset in enumerate(train_batched_datasets):

                step += 1
                seen_instances += len(batch_dataset)
                if step % logging_period == logging_period - 1:
                    trainer_result = tf_manager.execute(
                        batch_dataset, [trainer], train=True,
                        summaries=True)
                    train_results, train_outputs = run_on_dataset(
                        tf_manager, runners, batch_dataset,
                        postprocess, write_out=False)
                    train_evaluation = _evaluation(
                        evaluators, batch_dataset, runners,
                        train_results, train_outputs)

                    _log_continuons_evaluation(tb_writer, main_metric,
                                               train_evaluation,
                                               seen_instances,
                                               trainer_result,
                                               train=True)
                else:
                    tf_manager.execute(batch_dataset, [trainer],
                                       train=True, summaries=False)

                if step % validation_period == validation_period - 1:
                    val_results, val_outputs = run_on_dataset(
                        tf_manager, runners, val_dataset,
                        postprocess, write_out=False)
                    val_evaluation = _evaluation(
                        evaluators, val_dataset, runners, val_results,
                        val_outputs)

                    this_score = val_evaluation[main_metric]

                    def is_better(score1, score2, minimize):
                        if minimize:
                            return score1 < score2
                        else:
                            return score1 > score2

                    def argworst(scores, minimize):
                        if minimize:
                            return np.argmax(scores)
                        else:
                            return np.argmin(scores)

                    if is_better(this_score, best_score, minimize_metric):
                        best_score = this_score
                        best_score_epoch = i + 1
                        best_score_batch_no = batch_n

                    worst_index = argworst(saved_scores, minimize_metric)
                    worst_score = saved_scores[worst_index]

                    if is_better(this_score, worst_score, minimize_metric):
                        # we need to save this score instead the worst score
                        worst_var_file = variables_files[worst_index]
                        tf_manager.save(worst_var_file)
                        saved_scores[worst_index] = this_score
                        log("Variable file saved in {}".format(worst_var_file))

                        # update symlink
                        if best_score == this_score:
                            os.unlink(link_best_vars)
                            os.symlink(os.path.basename(worst_var_file),
                                       link_best_vars)

                        log("Best scores saved so far: {}".format(saved_scores))

                    log("Validation (epoch {}, batch number {}):"
                        .format(i + 1, batch_n), color='blue')

                    _log_continuons_evaluation(tb_writer, main_metric,
                                               val_evaluation, seen_instances,
                                               val_results, train=False)

                    if this_score == best_score:
                        best_score_str = colored("{:.2f}".format(best_score),
                                                 attrs=['bold'])
                    else:
                        best_score_str = "{:.2f}".format(best_score)

                    log("best {} on validation: {} (in epoch {}, "
                        "after batch number {})"
                        .format(main_metric, best_score_str,
                                best_score_epoch, best_score_batch_no),
                        color='blue')

                    log_print("")
                    _print_examples(val_dataset, val_outputs)
                    log_print("")

                    tb_writer.add_summary(val_plots[0], step)

    except KeyboardInterrupt:
        log("Training interrupted by user.")

    if os.path.islink(link_best_vars):
        tf_manager.restore(link_best_vars)

    log("Training finished. Maximum {} on validation data: {:.2f}, epoch {}"
        .format(main_metric, best_score, best_score_epoch))

    for dataset in test_datasets:
        test_results, test_outputs = run_on_dataset(
            tf_manager, runners, dataset, postprocess,
            write_out=True)
        evaluation = _evaluation(evaluators, dataset, runners,
                                 test_results, test_outputs)
        _print_final_evaluation(dataset.name, evaluation)

    log("Finished.")


def run_on_dataset(tf_manager: TensorFlowManager,
                   runners: List[BaseRunner],
                   dataset: Dataset,
                   postprocess,
                   write_out=False) -> Tuple[List[ExecutionResult],
                                             Dict[str, List[Any]]]:
    """Apply the model on a dataset and optionally write outputs to files.

    Args:
        tf_manager: TensorFlow manager with initialized sessions.
        runners: A function that runs the code
        dataset: The dataset on which the model will be executed.
        evaluators: List of evaluators that are used for the model
            evaluation if the target data are provided.
        postprocess: an object to use as postprocessing of the
        write_out: Flag whether the outputs should be printed to a file defined
            in the dataset object.

        extra_fetches: Extra tensors to evaluate for each batch.

    Returns:
        Tuple of resulting sentences/numpy arrays, and evaluation results if
        they are available which are dictionary function -> value.

    """

    contains_targets = all(dataset.has_series(runner.output_series)
                           for runner in runners)

    all_results = tf_manager.execute(dataset, runners,
                                     train=contains_targets)

    result_data_raw = {runner.output_series: result.outputs
                       for runner, result in zip(runners, all_results)}

    if postprocess is not None:
        result_data = postprocess(dataset, result_data_raw)
    else:
        result_data = result_data_raw

    if write_out:
        for series_id, data in result_data.items():
            if series_id in dataset.series_outputs:
                path = dataset.series_outputs[series_id]
                if isinstance(data, np.ndarray):
                    np.save(path, data)
                    log("Result saved as numpy array to \"{}\"".format(path))
                else:
                    with open(path, 'w') as f_out:
                        f_out.writelines(
                            [" ".join(sent) + "\n" for sent in data])
                    log("Result saved as plain text \"{}\"".format(path))
            else:
                log("There is no output file for dataset: {}"
                    .format(dataset.name), color='red')

    return all_results, result_data


def _evaluation(evaluators, dataset, runners, execution_results, result_data):
    """Evaluate the model outputs.

    Args:
        evaluators: List of tuples of series and evaluation function.
        dataset: Dataset against which the evaluation is done.
        runners: List of runners (contain series ids and loss names).
        execution_results: Execution results that include the loss values.
        result_data: Dictionary from series names to list of outputs.

    Returns:
        Dictionary of evaluation names and their values which includes the
        metrics applied on respective series loss and loss values from the run.
    """
    evaluation = {}

    # losses
    for runner, result in zip(runners, execution_results):
        for name, value in zip(runner.loss_names, result.losses):
            evaluation["{}/{}".format(runner.name, name)] = value

    # evaluation metrics
    for series_id, function in evaluators:
        if not dataset.has_series(series_id) or series_id not in result_data:
            continue

        desired_output = dataset.get_series(series_id)
        model_output = result_data[series_id]
        evaluation["{}/{}".format(series_id, function.name)] = function(
            model_output, desired_output)

    return evaluation


def _log_continuons_evaluation(tb_writer: tf.train.SummaryWriter,
                               main_metric: str,
                               eval_result: Evaluation,
                               seen_instances: int,
                               execution_results: List[ExecutionResult],
                               train=False) -> None:
    """Log the evaluation results and the TensorBoard summaries."""

    if train:
        color, prefix = 'yellow', 'train'
    else:
        color, prefix = 'blue', 'val'

    eval_string = _format_evaluation_line(eval_result, main_metric)
    log(eval_string, color=color)

    if tb_writer:
        for result in execution_results:
            for summaries in [result.scalar_summaries,
                              result.histogram_summaries,
                              result.image_summaries]:
                if summaries is not None:
                    tb_writer.add_summary(summaries, seen_instances)

        external_str = \
            tf.Summary(value=[tf.Summary.Value(tag=prefix + "_" + name,
                                               simple_value=value)
                              for name, value in eval_result.items()])
        tb_writer.add_summary(external_str, seen_instances)


def _format_evaluation_line(evaluation_res: Evaluation,
                            main_metric: str) -> str:
    """ Format the evaluation metric for stdout with last one bold."""
    eval_string = "    ".join("{}: {:.2f}".format(name, value)
                              for name, value in evaluation_res.items()
                              if name != main_metric)

    eval_string += colored(
        "    {}: {:.2f}".format(main_metric,
                                evaluation_res[main_metric]),
        attrs=['bold'])

    return eval_string


def _print_final_evaluation(name: str, evaluation: Evaluation) -> None:
    """Print final evaluation from a test dataset."""
    line_len = 22
    log("Evaluating model on \"{}\"".format(name))

    for name, value in evaluation.items():
        space = "".join([" " for _ in range(line_len - len(name))])
        log("... {}:{} {:.4f}".format(name, space, value))

    log_print("")


def _data_item_to_str(item: Any) -> str:
    if isinstance(item, list):
        return " ".join(item)
    elif isinstance(item, str):
        return item
    elif isinstance(item, np.ndarray):
        return "numpy tensor"
    else:
        return str(item)


def _print_examples(dataset: Dataset,
                    outputs: Dict[str, List[Any]],
                    num_examples=15) -> None:
    """Print examples of the model output."""
    log_print("Examples:")

    # for further indexing we need to make sure, all relevant
    # dataset series are lists
    dataset_series = {series_id: list(dataset.get_series(series_id))
                      for series_id in outputs.keys()}

    for i in range(num_examples):
        log_print("    [{}]".format(i + 1))
        for series_id, data in outputs.items():
            model_output = data[i]
            desired_output = dataset_series[series_id][i]

            # TODO: add some fancy colors
            log_print("    {}      :{}".format(
                series_id, _data_item_to_str(model_output)))
            log_print("    {} (ref):{}".format(
                series_id, _data_item_to_str(desired_output)))
            log_print("")
        log_print("")
