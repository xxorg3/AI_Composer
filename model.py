import os
import numpy as np
import tensorflow as tf    
from tensorflow.models.rnn import rnn_cell
from tensorflow.models.rnn import rnn, seq2seq

import nottingham_util

class Model(object):
    """ RNN Model """
    
    def __init__(self, config, training=False):
        self.time_batch_len = time_batch_len = config["time_batch_len"]
        self.input_dim = input_dim = config["input_dim"]
        hidden_size = config["hidden_size"]
        num_layers = config["num_layers"]
        dropout_prob = config["dropout_prob"]
        cell_type = config["cell_type"]

        print config

        if (dropout_prob <= 0.0 or dropout_prob > 1.0):
            raise Exception("Invalid dropout probability: {}".format(dropout_prob))

        # setup variables
        with tf.variable_scope("rnnlstm"):
            output_W = tf.get_variable("output_w", [hidden_size, input_dim])
            output_b = tf.get_variable("output_b", [input_dim])
            self.lr = tf.Variable(0.0, name="learning_rate", trainable=False)
            self.lr_decay = tf.Variable(0.0, name="learning_rate_decay", trainable=False)

        def create_cell(input_size, i):
            # with tf.variable_scope("hidden_{}".format(i)):
            if cell_type == "vanilla":
                return rnn_cell.BasicRNNCell(hidden_size, input_size = input_size)
            else:
                return rnn_cell.BasicLSTMCell(hidden_size, input_size = input_size)

        # if cell_type == "vanilla":
        #     first_layer = rnn_cell.BasicRNNCell(hidden_size, input_size = input_dim)  
        #     hidden_layer = rnn_cell.BasicRNNCell(hidden_size, input_size = hidden_size)
        # else:
        #     first_layer = rnn_cell.BasicLSTMCell(hidden_size, input_size = input_dim)  
        #     hidden_layer = rnn_cell.BasicLSTMCell(hidden_size, input_size = hidden_size)

        self.cell = rnn_cell.MultiRNNCell(
            [create_cell(input_dim, 0)] + [create_cell(hidden_size, i) for i in range(1, num_layers)])
        if training and dropout_prob < 1.0:
            self.cell = rnn_cell.DropoutWrapper(self.cell, output_keep_prob = dropout_prob)

        self.seq_input = \
            tf.placeholder(tf.float32, shape=[time_batch_len, None, input_dim])

        batch_size = tf.shape(self.seq_input)[0]
        self.initial_state = self.cell.zero_state(batch_size, tf.float32)
        inputs_list = tf.unpack(self.seq_input)

        # rnn outputs a list of [batch_size x H] outputs
        outputs_list, self.final_state = rnn.rnn(self.cell, inputs_list, 
                                                 initial_state=self.initial_state)

        # logits = tf.pack([tf.matmul(outputs_list[t], output_W) + output_b for t in range(time_batch_len)])

        # TODO: verify if the below is faster and correct
        outputs = tf.pack(outputs_list)
        outputs_concat = tf.reshape(outputs, [-1, hidden_size])
        logits_concat = tf.matmul(outputs_concat, output_W) + output_b
        logits = tf.reshape(logits_concat, [time_batch_len, -1, input_dim])

        # probabilities of each note
        self.probs = self.calculate_probs(logits)
        self.loss = self.init_loss(logits, logits_concat)
        self.train_step = tf.train.RMSPropOptimizer(self.lr, decay = self.lr_decay) \
                            .minimize(self.loss)

    def init_loss(self, outputs, _):
        self.seq_targets = \
            tf.placeholder(tf.float32, [self.time_batch_len, None, self.input_dim])

        batch_size = tf.shape(self.seq_input)
        cross_ent = tf.nn.sigmoid_cross_entropy_with_logits(outputs, self.seq_targets)
        return tf.reduce_sum(cross_ent) / self.time_batch_len / tf.to_float(batch_size)

    def calculate_probs(self, logits):
        return tf.sigmoid(logits)

    def assign_lr(self, session, lr_value):
        session.run(tf.assign(self.lr, lr_value))

    def assign_lr_decay(self, session, lr_decay_value):
        session.run(tf.assign(self.lr_decay, lr_decay_value))

    def get_cell_zero_state(self, session, batch_size):
        return self.cell.zero_state(batch_size, tf.float32).eval(session=session)

class NottinghamModel(Model):

    def init_loss(self, outputs, outputs_concat):
        self.seq_targets = \
            tf.placeholder(tf.int64, [self.time_batch_len, None, 2])
        batch_size = tf.shape(self.seq_targets)[1]

        with tf.variable_scope("rnnlstm"):
            self.lr = tf.Variable(0.0, name="learning_rate", trainable=False)
            self.lr_decay = tf.Variable(0.0, name="learning_rate_decay", trainable=False)
            self.melody_coeff = tf.Variable(0.5, name="melody_coeff", trainable=False)

        r = nottingham_util.NOTTINGHAM_MELODY_RANGE
        targets_concat = tf.reshape(self.seq_targets, [-1, 2])
        melody_loss = tf.nn.sparse_softmax_cross_entropy_with_logits( \
            outputs_concat[:, :r], \
            targets_concat[:, 0])
        harmony_loss = tf.nn.sparse_softmax_cross_entropy_with_logits( \
            outputs_concat[:, r:], \
            targets_concat[:, 1])
        losses = tf.add(self.melody_coeff * melody_loss, (1 - self.melody_coeff) * harmony_loss)

        return tf.reduce_sum(losses) / self.time_batch_len / tf.to_float(batch_size)

    def calculate_probs(self, logits):
        steps = []
        for t in range(self.time_batch_len):
            melody_softmax = tf.nn.softmax(logits[t, :, :nottingham_util.NOTTINGHAM_MELODY_RANGE])
            harmony_softmax = tf.nn.softmax(logits[t, :, nottingham_util.NOTTINGHAM_MELODY_RANGE:])
            steps.append(tf.concat(1, [melody_softmax, harmony_softmax]))
        return tf.pack(steps)

    def assign_melody_coeff(self, session, melody_coeff):
        if melody_coeff < 0.0 or melody_coeff > 1.0:
            raise Exception("Invalid melody coeffecient")

        session.run(tf.assign(self.melody_coeff, melody_coeff))

class NottinghamSeparate(Model):

    def init_loss(self, outputs, outputs_concat):
        self.seq_targets = \
            tf.placeholder(tf.int64, [self.time_batch_len, None])
        batch_size = tf.shape(self.seq_targets)[1]

        with tf.variable_scope("rnnlstm"):
            self.lr = tf.Variable(0.0, name="learning_rate", trainable=False)
            self.lr_decay = tf.Variable(0.0, name="learning_rate_decay", trainable=False)
            self.melody_coeff = tf.Variable(0.5, name="melody_coeff", trainable=False)

        targets_concat = tf.reshape(self.seq_targets, [-1])
        losses = tf.nn.sparse_softmax_cross_entropy_with_logits( \
            outputs_concat, targets_concat)

        return tf.reduce_sum(losses) / self.time_batch_len / tf.to_float(batch_size)

    def calculate_probs(self, logits):
        steps = []
        for t in range(self.time_batch_len):
            softmax = tf.nn.softmax(logits[t, :, :])
            steps.append(softmax)
        return tf.pack(steps)
