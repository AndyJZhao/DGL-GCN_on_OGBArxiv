from abc import abstractmethod, ABCMeta
from utils.util_funcs import exp_init, time_logger, print_log
from time import time
import torch as th
from utils.evaluation import eval_logits, eval_classification, save_results
import numpy as np
from dgl_implementation.run import Evaluator


class NodeClassificationTrainer(metaclass=ABCMeta):
    def __init__(self, model, g, features, optimizer, stopper, loss_func, sup, cf, evaluator):
        self.trainer = None
        self.model = model
        self.g = g.cpu()
        self.features = features
        self.optimizer = optimizer
        self.stopper = stopper
        self.loss_func = loss_func
        self.cf = cf
        self.device = cf.device
        self.epochs = cf.epochs
        self.n_class = cf.n_class
        self.__dict__.update(sup.__dict__)
        self.train_x, self.val_x, self.test_x = \
            [_.to(cf.device) for _ in [sup.train_x, sup.val_x, sup.test_x]]
        self.train_y, self.val_y, self.test_y = \
            [sup.labels.squeeze()[_].to(cf.device) for _ in [sup.train_x, sup.val_x, sup.test_x]]
        self.labels = sup.labels.to(cf.device)
        self.evaluator = evaluator

    @abstractmethod
    def _train(self):
        return None, None

    @abstractmethod
    def _evaluate(self):
        return None, None

    def run(self):
        for epoch in range(self.epochs):
            t0 = time()
            loss, train_acc = self._train()
            val_acc, test_acc = self._evaluate()
            print_log(epoch, {'Time': time() - t0, 'loss': loss,
                              'TrainAcc': train_acc, 'ValAcc': val_acc, 'TestAcc': test_acc})
            if self.stopper is not None:
                if self.stopper.step(val_acc, self.model, epoch):
                    print(f'Early stopped, loading model from epoch-{self.stopper.best_epoch}')
                    break
        if self.stopper is not None:
            self.model.load_state_dict(th.load(self.stopper.path))
        return self.model

    def eval_and_save(self):
        val_acc, test_acc = self._evaluate()
        res = {'test_acc': f'{test_acc:.4f}', 'val_acc': f'{val_acc:.4f}'}
        if self.stopper is not None: res['best_epoch'] = self.stopper.best_epoch
        save_results(self.cf, res)


class FullBatchTrainer(NodeClassificationTrainer):
    def __init__(self, **kwargs):
        super(FullBatchTrainer, self).__init__(**kwargs)
        self.g = self.g.to(self.device)
        self.features = self.features.to(self.device)

    def _train(self):
        self.model.train()
        self.optimizer.zero_grad()
        logits = self.model(self.g, self.features)
        loss = self.loss_func(logits[self.train_x], self.train_y)
        # loss = self.loss_func(logits[self.train_x], self.labels.squeeze()[self.train_x])
        train_acc, train_f1, train_mif1 = eval_logits(logits, self.train_x, self.train_y)
        loss.backward()
        self.optimizer.step()
        # return loss.item(), train_acc
        return loss.item(), train_acc

    @th.no_grad()
    def _evaluate(self):
        self.model.eval()
        logits = self.model(self.g, self.features)
        val_acc = self.evaluator(logits[self.val_x], self.labels[self.val_x])
        test_acc = self.evaluator(logits[self.test_x], self.labels[self.test_x])

        self.evaluator(logits[self.train_x], self.labels[self.train_x])
        # val_acc, val_f1, val_mif1 = eval_logits(logits, self.val_x, self.val_y)
        # test_acc, test_maf1, test_mif1 = eval_logits(logits, self.test_x, self.test_y)
        # print(f'DGL TestAcc{dgl_eval_acc(self.test_y.view((-1, 1)), logits[self.test_x].argmax(dim=-1, keepdim=True))}')

        return val_acc, test_acc


evaluator = lambda pred, labels: dgl_eval_acc(y_pred=pred.argmax(dim=-1, keepdim=True), y_true=labels)["acc"]


def dgl_eval_acc(y_true, y_pred):
    acc_list = []

    for i in range(y_true.shape[1]):
        is_labeled = y_true[:, i] == y_true[:, i]
        correct = y_true[is_labeled, i] == y_pred[is_labeled, i]
        # acc_list.append(float(np.sum(correct)) / len(correct))
        acc_list.append(correct.sum() / len(correct))

    return {'acc': sum(acc_list) / len(acc_list)}
