# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
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
# ==============================================================================

"""A binary to train CIFAR-10 using a single GPU.

Accuracy:
cifar10_train.py achieves ~86% accuracy after 100K steps (256 epochs of
data) as judged by cifar10_eval.py.

Speed: With batch_size 128.

System        | Step Time (sec/batch)  |     Accuracy
------------------------------------------------------------------
1 Tesla K20m  | 0.35-0.60              | ~86% at 60K steps  (5 hours)
1 Tesla K40m  | 0.25-0.35              | ~86% at 100K steps (4 hours)

Usage:
Please see the tutorial and website for how to download the CIFAR-10
data set, compile the program and train the model.

http://tensorflow.org/tutorials/deep_cnn/
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import os
import time

import tensorflow as tf

import cifar10_reusable
import cifar10_input_ensemble

FLAGS = tf.app.flags.FLAGS

tf.app.flags.DEFINE_string('train_dir', '/tmp/cifar10_train',
                           """Directory where to write event logs """
                           """and checkpoint.""")
tf.app.flags.DEFINE_integer('max_steps', 1000000,
                            """Number of batches to run.""")
tf.app.flags.DEFINE_boolean('log_device_placement', False,
                            """Whether to log device placement.""")
tf.app.flags.DEFINE_integer('log_frequency', 10,
                            """How often to log results to the console.""")
tf.app.flags.DEFINE_float('epsilon', 8.,
                          """Strength of adversarial examples.""")


def train():
  """Train CIFAR-10 for a number of steps."""
  with tf.Graph().as_default():
    global_step = tf.contrib.framework.get_or_create_global_step()

    # Get images and labels for CIFAR-10.
    # Force input pipeline to CPU:0 to avoid operations sometimes ending up on
    # GPU and resulting in a slow down.
    with tf.device('/cpu:0'):
      data_dir = os.path.join(FLAGS.data_dir, 'cifar-10-batches-bin')
      images, images_adv_thin, images_adv_wide, images_adv_tutorial, labels = cifar10_input_ensemble.distorted_inputs(data_dir=data_dir, batch_size=FLAGS.batch_size)

    # Build a Graph that computes the logits predictions from the
    # inference model.
    logits = cifar10_reusable.inference(images)

    # Calculate loss.
    loss_benign = cifar10_reusable.loss(logits, labels)

    def make_dynamic_adv_images():
      # Generate adversarial examples.
      noleak_labels = tf.argmax(logits, axis=1)
      noleak_loss = cifar10_reusable.loss(logits, noleak_labels)
      grads, = tf.gradients(noleak_loss, images)
      perturbation = FLAGS.epsilon * tf.sign(grads)
      adv_images = tf.stop_gradient(tf.clip_by_value(images + perturbation, 0., 255.))
      return adv_images
    # The process freezes if we really do this conditionally.
    dynamic_adv_images = make_dynamic_adv_images()

    def make_poison():
      return tf.fill([FLAGS.batch_size, 32, 32, 3], float('NaN'))

    # Choose a set of adversarial examples at random.
    adv_choice = tf.random_uniform([], maxval=3, dtype=tf.int32, name='adv_choice')
    adv_images = tf.case([
      (tf.equal(adv_choice, 0), lambda: dynamic_adv_images),
      # images_adv_thin is held out
      (tf.equal(adv_choice, 1), lambda: images_adv_wide),
      (tf.equal(adv_choice, 2), lambda: images_adv_tutorial),
    ], default=make_poison)
    tf.summary.image('adv_images', adv_images)

    # Add loss on adversarial examples
    adv_logits = cifar10_reusable.inference(adv_images)
    loss_adv = cifar10_reusable.loss(adv_logits, labels)
    loss_total = loss_benign + loss_adv

    # Build a Graph that trains the model with one batch of examples and
    # updates the model parameters.
    train_op = cifar10_reusable.train(loss_total, global_step)

    precision = tf.reduce_mean(tf.cast(tf.equal(tf.cast(tf.argmax(logits, axis=1), tf.int32), labels), tf.float32))
    tf.summary.scalar('precision', precision)

    class _LoggerHook(tf.train.SessionRunHook):
      """Logs loss and runtime."""

      def begin(self):
        self._step = -1
        self._start_time = time.time()

      def before_run(self, run_context):
        self._step += 1
        return tf.train.SessionRunArgs(loss_total)  # Asks for loss value.

      def after_run(self, run_context, run_values):
        if self._step % FLAGS.log_frequency == 0:
          current_time = time.time()
          duration = current_time - self._start_time
          self._start_time = current_time

          loss_value = run_values.results
          examples_per_sec = FLAGS.log_frequency * FLAGS.batch_size / duration
          sec_per_batch = float(duration / FLAGS.log_frequency)

          format_str = ('%s: step %d, loss = %.2f (%.1f examples/sec; %.3f '
                        'sec/batch)')
          print (format_str % (datetime.now(), self._step, loss_value,
                               examples_per_sec, sec_per_batch))

    with tf.train.MonitoredTrainingSession(
        checkpoint_dir=FLAGS.train_dir,
        hooks=[tf.train.StopAtStepHook(last_step=FLAGS.max_steps),
               tf.train.NanTensorHook(loss_total),
               _LoggerHook()],
        config=tf.ConfigProto(
            log_device_placement=FLAGS.log_device_placement)) as mon_sess:
      while not mon_sess.should_stop():
        mon_sess.run(train_op)


def main(argv=None):  # pylint: disable=unused-argument
  cifar10_reusable.maybe_download_and_extract()
  if tf.gfile.Exists(FLAGS.train_dir):
    tf.gfile.DeleteRecursively(FLAGS.train_dir)
  tf.gfile.MakeDirs(FLAGS.train_dir)
  train()


if __name__ == '__main__':
  tf.app.run()