"""Define the model."""

import tensorflow as tf


def build_model(mode, inputs, is_training, params):
    """Compute logits of the model (output distribution)

    Args:
        mode: (string) can be 'train' or 'eval'
        inputs: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        params: (Params) hyperparameters

    Returns:
        output: (tf.Tensor) output of the model
    """

    prices = inputs['prices']
    print('prices shape: ', prices.get_shape())

    def lstm_cell():
        return tf.nn.rnn_cell.BasicLSTMCell(params.lstm_num_units, forget_bias=1.0)

    # Shallow LSTM model
    if params.model_version == 'lstm1':
        # Apply LSTM over the price features
        lstm_cell = tf.contrib.rnn.DropoutWrapper(lstm_cell(), output_keep_prob=(1.0 - params.dropout_rate))
        output, _ = tf.nn.dynamic_rnn(lstm_cell, prices, dtype=tf.float32)
        output = tf.reshape(output, (-1, output.get_shape()[1]*output.get_shape()[2]))

        # Compute logits from the output of the LSTM
        output = tf.layers.dense(output, 60, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        dropout = tf.layers.dropout(output, rate=params.dropout_rate, training=is_training)
        output2 = tf.layers.dense(dropout, 30, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        output3 = tf.layers.dense(output2, 20, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        logits = tf.layers.dense(output3, 1)

    # Deep LSTM model
    elif params.model_version == 'lstm2':
        # Apply LSTM over the price features
        stacked_lstm = tf.contrib.rnn.MultiRNNCell([lstm_cell() for _ in range(params.lstm_layers)])
        output, _ = tf.nn.dynamic_rnn(stacked_lstm, prices, dtype=tf.float32)
        output = tf.reshape(output, (-1, output.get_shape()[1]*output.get_shape()[2]))

        # Compute logits from the output of the LSTM
        output = tf.layers.dense(output, 60, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        dropout = tf.layers.dropout(output, rate=params.dropout_rate, training=is_training)
        output2 = tf.layers.dense(dropout, 30, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        output3 = tf.layers.dense(output2, 20, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        logits = tf.layers.dense(output3, 1)

    elif params.model_version == 'conv':
        window_lengths = [3, 7, 11, 15, 19, 23, 27, 31, 35]
        feature_maps = []
        for wlength in window_lengths:
            fmap = tf.layers.conv1d(
                prices,
                params.nb_filters,
                wlength,
                strides=1,
                padding='same',
                activation='relu',
                kernel_regularizer=\
                tf.contrib.layers.l2_regularizer(params.l2_reg))

            if conv_dropout > 0.0:
                fmap = tf.layers.dropout(fmap, rate=params.dropout)
            feature_maps.append(fmap)


        x = tf.concat(feature_maps, axis=-1)
        x = tf.reduce_max(x, axis=1)
        output = tf.layers.dense(x, 60, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        dropout = tf.layers.dropout(output, rate=params.dropout_rate, training=is_training)
        output2 = tf.layers.dense(dropout, 30, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        output3 = tf.layers.dense(output2, 20, activation=tf.tanh, kernel_regularizer=tf.contrib.layers.l2_regularizer(params.l2_reg))
        logits = tf.layers.dense(output3, 1)

    else:
        raise NotImplementedError("Unknown model version: {}".format(params.model_version))

    return logits


def model_fn(mode, inputs, params, reuse=False):
    """Model function defining the graph operations.

    Args:
        mode: (string) can be 'train' or 'eval'
        inputs: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        params: (Params) contains hyperparameters of the model (ex: `params.learning_rate`)
        reuse: (bool) whether to reuse the weights

    Returns:
        model_spec: (dict) contains the graph operations or nodes needed for training / evaluation
    """
    is_training = (mode == 'train')
    train_placeholder = tf.placeholder(tf.bool)
    deltas = inputs['deltas']
    deltas = tf.cast(deltas, tf.float32)

    # -----------------------------------------------------------
    # MODEL: define the layers of the model
    with tf.variable_scope('model', reuse=reuse):
        # Compute the output distribution of the model and the predictions
        logits = build_model(is_training, inputs, train_placeholder, params)
        predictions = tf.reshape(tf.cast(logits > 0.0, tf.int32), (-1, 1))

    # Labels (+1 if delta > 0 and 0 otherwise)
    labels = tf.reshape(tf.cast(deltas, tf.float32), (-1, 1))

    # Define loss, accuracy, and profit
    loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits))

    accuracy = tf.reduce_mean(tf.cast(tf.equal(tf.cast(labels, tf.int32), predictions), tf.float32))

    profit = tf.multiply(logits, deltas)
    profit = tf.reduce_mean(profit)

    # Define training step that minimizes the loss with the Adam optimizer
    if is_training:
        optimizer = tf.train.AdamOptimizer(params.learning_rate)
        global_step = tf.train.get_or_create_global_step()
        train_op = optimizer.minimize(loss, global_step=global_step)


    # -----------------------------------------------------------
    # METRICS AND SUMMARIES
    # Metrics for evaluation using tf.metrics (average over whole dataset)
    with tf.variable_scope("metrics"):
        metrics = {
            'loss': tf.metrics.mean(loss),
            'accuracy': tf.metrics.accuracy(labels=labels, predictions=predictions)
        }

    # Group the update ops for the tf.metrics
    update_metrics_op = tf.group(*[op for _, op in metrics.values()])

    # Get the op to reset the local variables used in tf.metrics
    metric_variables = tf.get_collection(tf.GraphKeys.LOCAL_VARIABLES, scope="metrics")
    metrics_init_op = tf.variables_initializer(metric_variables)

    # Summaries for training
    tf.summary.scalar('loss', loss)
    tf.summary.scalar('accuracy', accuracy)
    tf.summary.scalar('profit', profit)

    # -----------------------------------------------------------
    # MODEL SPECIFICATION
    # Create the model specification and return it
    # It contains nodes or operations in the graph that will be used for training and evaluation
    model_spec = inputs
    variable_init_op = tf.group(*[tf.global_variables_initializer(), tf.tables_initializer()])
    model_spec['variable_init_op'] = variable_init_op
    model_spec['is_training'] = train_placeholder
    model_spec['predictions'] = predictions
    model_spec['deltas'] = deltas
    model_spec['labels'] = labels
    model_spec['loss'] = loss
    model_spec['accuracy'] = accuracy
    model_spec['profit'] = profit
    model_spec['metrics_init_op'] = metrics_init_op
    model_spec['metrics'] = metrics
    model_spec['update_metrics'] = update_metrics_op
    model_spec['summary_op'] = tf.summary.merge_all()

    if is_training:
        model_spec['train_op'] = train_op

    return model_spec
