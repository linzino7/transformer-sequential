#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import random
import math
import torch
import torch.nn.functional as F

a = 0 # right
b = 0 # error

c = 0
d = 0


def compute_masked_loss(args, out, Y, corpus, aux_loss):
    # merge batch dim and temporal dim
    out = out.view(-1, out.size(-1))
    Y = Y.view(-1)

    # do not train on specified output tokens
    mask = False
    for w in args.data_omit_label_idx:
        mask += Y.eq(w)
    mask = 1 - mask.float()

    # compute loss
    loss = F.nll_loss(out, Y, reduction="none")

    loss = loss * mask
    loss = loss.sum() / (mask.sum() + 1e-6)
    if torch.is_tensor(aux_loss):
        if args.expire_span:
            # this loss has no correspondance to input tokens
            aux_loss = aux_loss.mean()
        else:
            aux_loss = aux_loss.view(-1)
            aux_loss = aux_loss * mask
            aux_loss = aux_loss.sum() / (mask.sum() + 1e-6)

    if hasattr(corpus, "train_labels"):
        # compute acc
        _, pred = out.max(dim=1)
        err = Y.ne(pred).float()
        err = err * mask
        err = err.sum() / (mask.sum() + 1e-6)
    else:
        err = -1
    return loss, aux_loss, err


def compute_total_loss(args, out, Y, corpus, aux_loss):
    global a, b, c ,d
    if args.data_omit_label_idx is not None:
        return compute_masked_loss(args, out, Y, corpus, aux_loss)

    #print('out size: ', out.size())
    #print(out[0][0])
    
    num_candidates = 4
    tmp = torch.argsort(out, dim=2)
    #print('tmp:' ,tmp[0][0])
    #print(tmp.size())
    

    # candidates method
    flog2 = True 
    for i in range(0,Y.size(0)):
        for j in range(0, len(tmp[i])):
            # if j == 0: print(Y[i][j])
            # if j == 0: print(tmp[i][j][-num_candidates:])
            if not Y[i][j] in tmp[i][j][-num_candidates:]:
                flog2 = False
                #print(Y[i][j])
                #print(tmp[i][j][-num_candidates:])

        if 7 in Y[i] and flog2:
            c = c + 1
        elif 7 in Y [i]:
            d = d + 1



    outputlabel = out.max(dim=2).indices # get pridice y
    #print(outputlabel[0][0])


    #print('===out===')
    #print(out)
    #print(out.size())
    #print('out:', outputlabel)
    #print('outmax:', outputlabel.size())
    #print(outputlabel[0:256])
    #print("===Y===")
    #print('Y:', Y)
    #print(Y.size())
    #print(Y.size(0))
    #raise

    flog = True
    for i in range(0,Y.size(0)):
        #print('outputlabel[i]',outputlabel[i])
        #print('Y[i]',Y[i])
        if not outputlabel[i].equal(Y[i]):
            flog = False
    
        if 7 in Y[i] and flog:
            a = a + 1
        elif 7 in Y [i]:
            b = b + 1 
    #if a > 20 :
    #print("a ,b: ",a,b)
    #raise
    

    
    # merge batch dim and temporal dim
    out = out.view(-1, out.size(-1))
    Y = Y.view(-1)

    # compute loss
    loss = F.nll_loss(out, Y)
    
    #print('loss:',loss)

    if torch.is_tensor(aux_loss):
        aux_loss = aux_loss.mean()

    if hasattr(corpus, "train_labels"):
        # compute acc
        _, pred = out.max(dim=1)
        err = Y.ne(pred).float().mean()
    else:
        err = -1
    return loss, aux_loss, err


# separating batch training reduces memory usage (removes overlap?)
def train_batch(
    args,
    model,
    optimizer,
    scheduler,
    X,
    Y,
    h_cache,
    stat,
    test_only=False,
    update=True,
    corpus=None,
):
    out, h_cache, aux_loss = model(X, h_cache, Y)

    for i in range(len(h_cache)):
        h_cache[i] = h_cache[i].detach()

    loss, aux_loss, err = compute_total_loss(args, out, Y, corpus, aux_loss)
    
    #print(args.data_omit_label_idx)
    #raise # zino

    stat["loss"] = stat.get("loss", 0) + loss.item()
    if err >= 0:
        stat["err"] = stat.get("err", 0) + err.item()
    if not test_only:  
        loss = loss + aux_loss
        if hasattr(model.module, "layers"):
            for l in model.module.layers:
                if args.adapt_span:
                    loss = loss + l.attn.attn.adaptive_span.get_loss()

        if scheduler is not None:
            scheduler.step()
            if args.lr_decay and args.lr_warmup > scheduler.last_epoch:
                # do warm-up manually
                for pg in optimizer.param_groups:
                    pg["lr"] = args.lr * scheduler.last_epoch / args.lr_warmup

        loss = loss / args.update_freq  # if the batch is split
        if update:
            loss.backward()
        else:
            with model.no_sync():
                loss.backward()

        if update:
            if args.grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()
            optimizer.zero_grad()

        if hasattr(model.module, "layers"):
            for l in model.module.layers:
                if args.adapt_span:
                    l.attn.attn.adaptive_span.param_clamp()

    return h_cache, loss # add loss


def train(
    args,
    model,
    optimizer,
    scheduler,
    data,
    test_only=False,
    train_pos=-1,
    h_cache=None,
    corpus=None,
):
    global a,b,c,d
    a = 0
    b = 0
    c = 0
    d = 0
    #print('x size:', data.size())
    
    labels = None
    if isinstance(data, tuple):
        data, labels = data

    stat = dict()
    if test_only:
        model.eval()
    else:
        model.train()
        optimizer.zero_grad()

    nbatches_max = args.nbatches
    if test_only:
        if args.full_test or args.full_valid:
            nbatches_max = data.size(1)
        else:
            # test on fewer batches for speed-up
            nbatches_max = max(1, args.nbatches // 10)
            # no need to test more than the whole dataset
            nbatches_max = min(nbatches_max, math.floor(data.size(1) / args.mem_sz))

    pbar = None
    if args.full_test:
        if args.rank == 0:
            from tqdm import tqdm

            pbar = tqdm(total=data.size(1))

    pos_max = data.size(1) - args.mem_sz
    if labels is not None:
        pos_max += 1

    nbatches = 0
    for batch_ind in range(nbatches_max):
        
        offset = train_pos
        if pbar:
            pbar.update(args.mem_sz)

        nbatches += 1
        update = nbatches % args.update_freq == 0

        X = data[:, offset : offset + args.mem_sz]
        
        X = (
            X.to(args.device).contiguous().long()
            if args.lazy_load_data
            else X.contiguous().long()
        )
        if labels is None:
            #Y = data[:, offset + 1 : offset + args.mem_sz + 1]
            Y = data[:, offset : offset + args.mem_sz]
        else:
            Y = labels[:, offset : offset + args.mem_sz]
        Y = (
            Y.to(args.device).contiguous().long()
            if args.lazy_load_data
            else Y.contiguous().long()
        )
        h_cache, loss = train_batch(
            args,
            model,
            optimizer,
            scheduler,
            X,
            Y,
            h_cache,
            stat,
            test_only,
            update,
            corpus,
        )
        
        #if batch_ind % 1000 ==0:
        #    print('nbatch:', batch_ind)
        #    print('loss:',loss)
        
        if train_pos >= 0:
            train_pos += args.mem_sz
            if train_pos >= pos_max:
                if args.full_test or (args.full_valid and test_only):
                    # only test once
                    break
                elif test_only:
                    train_pos = 0
                else:
                    # randomize offset to reduce overfitting
                    train_pos = random.randrange(args.mem_sz)
                h_cache = model.module.init_hid_cache(data.size(0))
        #if nbatches >5: break
    print('==result==')
    print('a:',a)
    print('b:',b)
    print('c:',c)
    print('d:',d)
    
    if pbar:
        pbar.close()
    for k, v in stat.items():
        stat[k] = v / nbatches
    return stat, train_pos, h_cache
