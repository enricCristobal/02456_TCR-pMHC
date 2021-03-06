import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import torch
import math
from sklearn.metrics import accuracy_score, accuracy_score, roc_auc_score, roc_curve, auc
import random
from sklearn.decomposition import PCA

seed_val = 42
random.seed(seed_val)
np.random.seed(seed_val)
torch.manual_seed(seed_val)
torch.cuda.manual_seed_all(seed_val)
torch.use_deterministic_algorithms(True)


def return_aa(one_hot):
    mapping = dict(zip(range(20),"ACDEFGHIKLMNPQRSTVWY"))
    try:
        index = one_hot.index(1)
        return mapping[index]     
    except:
        return 'X'
    
    
def extract_aa_and_energy_terms(dataset_X):
    new_dataset_X = list()
    for cmplx in range(len(dataset_X)):
        df = pd.DataFrame(dataset_X[cmplx])
        df['aa'] = 'A'
        for i in range(len(df)):
            df['aa'][i] = return_aa(list(df.iloc[i,0:20]))
        df = df.iloc[:,20:]
        new_dataset_X.append(np.array(df))
    return np.array(new_dataset_X)


def extract_energy_terms(dataset_X):
    all_en = [np.concatenate((arr[0:190,20:], arr[192:,20:]), axis=0) for arr in dataset_X]  # 178
    return all_en

def reverseOneHot(encoding):
    """
    Converts one-hot encoded array back to string sequence
    """
    mapping = dict(zip(range(20),"ACDEFGHIKLMNPQRSTVWY"))
    seq=''
    for i in range(len(encoding)):
        if np.max(encoding[i])>0:
            seq+=mapping[np.argmax(encoding[i])]
    return seq

def extract_sequences(dataset_X, merge=False):
    """
    Return DataFrame with MHC, peptide and TCR a/b sequences from
    one-hot encoded complex sequences in dataset X
    """
    mhc_sequences = [reverseOneHot(arr[0:179,0:20]) for arr in dataset_X]
    pep_sequences = [reverseOneHot(arr[179:190,0:20]) for arr in dataset_X]
    tcr_sequences = [reverseOneHot(arr[192:,0:20]) for arr in dataset_X]
    all_sequences = [reverseOneHot(arr[:,0:20]) for arr in dataset_X]

    if merge:
        df_sequences = pd.DataFrame({"all": all_sequences})
        df_sequences = df_sequences.to_numpy().reshape(len(all_sequences))

    else:
        df_sequences = pd.DataFrame({"MHC":mhc_sequences,
                                 "peptide":pep_sequences,
                                 "tcr":tcr_sequences})

    return df_sequences

def load_peptide_target(filename):
    """
    Read amino acid sequence of peptides and
    corresponding log transformed IC50 binding values from text file.
    """
    df = pd.read_csv(filename, sep='\s+', usecols=[0,1], names=['peptide','target'])
    return df.sort_values(by='target', ascending=False).reset_index(drop=True)

def invoke(early_stopping, loss, model, implement=False):
    if implement == False:
        return False
    else:
        early_stopping(loss, model)
        if early_stopping.early_stop:
            #print("Early stopping")
            return True

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

def prepare_data_pca(embedded_list):
    """
    Get result from embedded to have the proper size in 2D 
    ready to run the PCA analysis afterwards.
    """
    n_observations = len(embedded_list)
    n_residues = len(embedded_list[0])
    embedded_att = len(embedded_list[0][0])
    embedded_matrix = torch.tensor(embedded_list).reshape(n_observations * n_residues, embedded_att).numpy()
    return n_observations, embedded_matrix

def run_PCA(data, variance_required = 0.9, max_components = 100):
    """
    Run PCA and get the minimum number of components required to reach 
    the minimum variance required.
    """
    # add condition for baseline model where its just has 54 components
    #if data.shape[1] < max_components:
    #    max_components = int(data.shape[1])
    
    first_model = PCA(n_components = max_components)
    first_model.fit_transform(data)

    variances = first_model.explained_variance_ratio_.cumsum()
    optimal_components = np.argmax(variances > variance_required)

    reduced_model = PCA(n_components = optimal_components)
    fitted_data = reduced_model.fit_transform(data)

    return variances, optimal_components, reduced_model, fitted_data

def back_to_original_size(matrix, final_size):
    """
    Reshape matrix back to original 3D shape with the reduced dimensionality
    for the embedded variable. 
    """
    final_tensor = torch.tensor(matrix).reshape(final_size[0], final_size[1], final_size[2]).numpy()
    return final_tensor

def construct_pssm(data):
    beta = 50.0
    peptides = data
    sequence_weighting = True
    file_name = f"../data/Matrices/PSSM"
    alphabet = np.loadtxt(f"../data/Matrices/alphabet", dtype=str)
    _bg = np.loadtxt(f"../data/Matrices/bg.freq.fmt", dtype=float)
    bg = {}
    for i in range(0, len(alphabet)):
        bg[alphabet[i]] = _bg[i]
    _blosum62 = np.loadtxt(f"../data/Matrices/blosum62.freq_rownorm", dtype=float).T
    blosum62 = {}

    for i, letter_1 in enumerate(alphabet):
        blosum62[letter_1] = {}
        for j, letter_2 in enumerate(alphabet):
            blosum62[letter_1][letter_2] = _blosum62[i, j]

    if len(peptides[0]) == 1:
        peptide_length = len(peptides)
        peptides = [peptides]
    else:
        peptide_length = len(peptides[0])

    for i in range(0, len(peptides)):
        if len(peptides[i]) != peptide_length:
            print("Error, peptides differ in length!")

    #print peptides

    def initialize_matrix(peptide_length, alphabet):
        init_matrix = [0]*peptide_length
        for i in range(0, peptide_length):
            row = {}
            for letter in alphabet:
                row[letter] = 0.0
            #fancy way:  row = dict( zip( alphabet, [0.0]*len(alphabet) ) )
            init_matrix[i] = row
        return init_matrix


    # ## Amino Acid Count Matrix (c)

    c_matrix = initialize_matrix(peptide_length, alphabet)
    for position in range(0, peptide_length):
        for peptide in peptides:
            c_matrix[position][peptide[position]] += 1

    # ## Sequence Weighting
    weights = {}
    for peptide in peptides:

        # apply sequence weighting
        if sequence_weighting:
            w = 0.0
            neff = 0.0
            for position in range(0, peptide_length):
                r = 0
                for letter in alphabet:
                    if c_matrix[position][letter] != 0:
                        r += 1
                s = c_matrix[position][peptide[position]]
                w += 1.0/(r * s)
                neff += r
            neff = neff / peptide_length
        # do not apply sequence weighting
        else:
            w = 1
            neff = len(peptides)
        weights[peptide] = w


    # ## Observed Frequencies Matrix (f)

    f_matrix = initialize_matrix(peptide_length, alphabet)
    for position in range(0, peptide_length):
        n = 0;
        for peptide in peptides:
            f_matrix[position][peptide[position]] += weights[peptide]
            n += weights[peptide]
        for letter in alphabet:
            f_matrix[position][letter] = f_matrix[position][letter]/n


    # ## Pseudo Frequencies Matrix (g)
    g_matrix = initialize_matrix(peptide_length, alphabet)
    for position in range(0, peptide_length):
        for letter_1 in alphabet:
            for letter_2 in alphabet:
                g_matrix[position][letter_1] += f_matrix[position][letter_2] * blosum62[letter_1][letter_2]


    # ## Combined Frequencies Matrix (p)
    p_matrix = initialize_matrix(peptide_length, alphabet)
    alpha = neff - 1
    for position in range(0, peptide_length):
        for a in alphabet:
            p_matrix[position][a] = (alpha*f_matrix[position][a] + beta*g_matrix[position][a]) / (alpha + beta)

    # ## Log Odds Weight Matrix (w)
    w_matrix = initialize_matrix(peptide_length, alphabet)
    for position in range(0, peptide_length):
        for letter in alphabet:
            if p_matrix[position][letter] > 0:
                w_matrix[position][letter] = 2 * math.log(p_matrix[position][letter]/bg[letter])/math.log(2)
            else:
                w_matrix[position][letter] = 0


    # ### Write Matrix to PSI-BLAST format
    # ### convert w_matrix to PSI-BLAST format and print to file

    def to_psi_blast_file(matrix, file_name):
        with open(file_name, 'w') as file:
            file.write("A\tR\tN\tD\tC\tQ\tE\tG\tH\tI\tL\tK\tM\tF\tP\tS\tT\tW\tY\tV\n")
            letter_order = ["A", "R", "N", "D", "C", "Q", "E", "G", "H", "I", "L", "K", "M", "F", "P", "S", "T", "W", "Y", "V"]
            for i, row in enumerate(matrix):
                scores = []
                #scores.append(str(i+1) + " A")
                #scores.append(str(i+1))
                for letter in letter_order:
                    score = row[letter]
                    scores.append(round(score, 4))
                file.write(str(scores[0])+"\t"+str(scores[1])+"\t"+str(scores[2])+"\t"+str(scores[3])+"\t"+
                          str(scores[4])+"\t"+str(scores[5])+"\t"+str(scores[6])+"\t"+str(scores[7])+"\t"+
                          str(scores[8])+"\t"+str(scores[9])+"\t"+str(scores[10])+"\t"+str(scores[11])+"\t"+
                          str(scores[12])+"\t"+str(scores[13])+"\t"+str(scores[14])+"\t"+str(scores[15])+"\t"+
                          str(scores[16])+"\t"+str(scores[17])+"\t"+str(scores[18])+"\t"+str(scores[19])+"\n")

    to_psi_blast_file(w_matrix, file_name)



def plot_losses(valid_loss,train_loss,burn_in=20):
    plt.figure(figsize=(15,4))
    plt.plot(list(range(burn_in, len(train_loss))), train_loss[burn_in:], label='Training loss')
    plt.plot(list(range(burn_in, len(valid_loss))), valid_loss[burn_in:], label='Validation loss')

    # find position of lowest validation loss
    minposs = valid_loss.index(min(valid_loss))+1
    plt.axvline(minposs, linestyle='--', color='r',label='Minimum Validation Loss')

    plt.legend(frameon=False)
    plt.xlabel('Epochs')
    plt.ylabel('Loss')
    plt.show()

def plot_roc_curve(fpr,tpr,roc_auc,peptide_length=[9]):
    plt.title('Receiver Operating Characteristic')
    plt.plot(fpr, tpr, label = 'AUC = %0.2f (%smer)' %(roc_auc, '-'.join([str(i) for i in peptide_length])))
    plt.legend(loc = 'lower right')
    plt.plot([0, 1], [0, 1], c='black', linestyle='--')
    plt.ylabel('True Positive Rate')
    plt.xlabel('False Positive Rate')

def plot_mcc(y_test,pred,mcc):
    plt.title('Matthews Correlation Coefficient')
    plt.scatter(y_test.flatten().detach().numpy(), pred.flatten().detach().numpy(), label = 'MCC = %0.2f' % mcc)
    plt.legend(loc = 'lower right')
    plt.ylabel('Predicted')
    plt.xlabel('Validation targets')
    plt.show()

#from pytorchTools
class EarlyStopping:
    """Early stops the training if validation loss doesn't improve after a given patience."""
    def __init__(self, patience=300, verbose=False, delta=0, path='checkpoint.pt'):
        """
        Args:
            patience (int): How long to wait after last time validation loss improved.
                            Default: 7
            verbose (bool): If True, prints a message for each validation loss improvement.
                            Default: False
            delta (float): Minimum change in the monitored quantity to qualify as an improvement.
                            Default: 0
            path (str): Path for the checkpoint to be saved to.
                            Default: 'checkpoint.pt'
        """
        self.patience = patience
        self.verbose = verbose
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.val_loss_min = np.Inf
        self.delta = delta
        self.path = path

    def __call__(self, val_loss, model):

        score = -val_loss

        if self.best_score is None:
            self.best_score = score
            self.save_checkpoint(val_loss, model)
        elif score < self.best_score + self.delta:
            self.counter += 1
            #print(f'EarlyStopping counter: {self.counter} out of {self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = score
            self.save_checkpoint(val_loss, model)
            self.counter = 0

    def save_checkpoint(self, val_loss, model):
        '''Saves model when validation loss decrease.'''
        if self.verbose:
            print(f'Validation loss decreased ({self.val_loss_min:.6f} --> {val_loss:.6f}).  Saving model ...')
        torch.save(model.state_dict(), self.path)
        self.val_loss_min = val_loss


def train_project(net, optimizer, train_ldr, val_ldr, test_ldr, X_valid, epochs, criterion, early_stop):
    num_epochs = epochs

    train_acc = []
    valid_acc = []
    test_acc = []

    train_losses = []
    valid_losses = []
    test_loss = []

    train_auc = []
    valid_auc = []
    test_auc = []

    no_epoch_improve = 0
    min_val_loss = np.Inf

    test_probs, test_preds, test_targs, test_peptides = [], [], [], []

    for epoch in range(num_epochs):
        cur_loss = 0
        val_loss = 0
        # Train
        net.train()
        train_preds, train_targs, train_probs = [], [], []
        for batch_idx, (data, target) in enumerate(train_ldr):
            X_batch = data.float().detach().requires_grad_(True)
            target_batch = torch.tensor(np.array(target), dtype=torch.float).unsqueeze(1)

            optimizer.zero_grad()
            output = net(X_batch)
            batch_loss = criterion(output, target_batch)
            batch_loss.backward()
            optimizer.step()

            probs = torch.sigmoid(output.detach())
            preds = np.round(probs.cpu())
            train_probs += list(probs.data.cpu().numpy())
            train_targs += list(np.array(target_batch.cpu()))
            train_preds += list(preds.data.numpy())
            cur_loss += batch_loss.detach()

        train_losses.append(cur_loss / len(train_ldr.dataset))

        net.eval()
        # Validation
        val_preds, val_targs, val_probs = [], [], []
        with torch.no_grad():
            for batch_idx, (data, target) in enumerate(val_ldr):
                x_batch_val = data.float().detach()
                y_batch_val = target.float().detach().unsqueeze(1)

                output = net(x_batch_val)
                val_batch_loss = criterion(output, y_batch_val)

                probs = torch.sigmoid(output.detach())
                preds = np.round(probs.cpu())
                val_probs += list(probs.data.cpu().numpy())
                val_preds += list(preds.data.numpy())
                val_targs += list(np.array(y_batch_val.cpu()))
                val_loss += val_batch_loss.detach()

            valid_losses.append(val_loss / len(val_ldr.dataset))

            train_acc_cur = accuracy_score(train_targs, train_preds)
            valid_acc_cur = accuracy_score(val_targs, val_preds)
            train_auc_cur = roc_auc_score(train_targs, train_probs)
            valid_auc_cur = roc_auc_score(val_targs, val_probs)

            train_acc.append(train_acc_cur)
            valid_acc.append(valid_acc_cur)
            train_auc.append(train_auc_cur)
            valid_auc.append(valid_auc_cur)

        # Early stopping
        if (val_loss / len(X_valid)).item() < min_val_loss:
            no_epoch_improve = 0
            min_val_loss = (val_loss / len(X_valid))
        else:
            no_epoch_improve += 1
        if no_epoch_improve == early_stop:
            print("Early stopping\n")
            break

        if epoch % 5 == 0:
            print("Epoch {}".format(epoch),
                  " \t Train loss: {:.5f} \t Validation loss: {:.5f}".format(train_losses[-1], valid_losses[-1]))

    # Test
    if test_ldr != []:

        with torch.no_grad():
            for batch_idx, (data, target) in enumerate(test_ldr):
                print(batch_idx)
                x_batch_test = data.float().detach()
                y_batch_test = target.float().detach().unsqueeze(1)

                output = net(x_batch_test)
                test_batch_loss = criterion(output, y_batch_test)

                probs = torch.sigmoid(output.detach())
                predsROC = probs
                preds = np.round(probs.cpu())
                test_probs = list(probs.data.cpu().numpy())
                test_preds = list(preds.data.numpy())
                test_predsROC = list(probs.data.cpu().numpy())
                print("-----",test_predsROC)
                test_targs = list(np.array(y_batch_test.cpu()))
                test_loss = test_batch_loss.detach()
                test_auc_cur = roc_auc_score(test_targs, test_predsROC)
                test_acc_cur = accuracy_score(test_targs, test_preds)
                test_acc.append(test_acc_cur)
                test_auc.append(test_auc_cur)

    return train_acc, train_losses, train_auc, valid_acc, valid_losses, valid_auc, val_preds, val_targs, test_preds, list(
        test_targs), test_loss, test_acc, test_auc, test_predsROC
