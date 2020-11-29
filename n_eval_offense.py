import numpy as np
import torch

from transformers import *
import run_generation as rg
from torch.nn import functional as F
import copy_of_train_npi_forn8_MAY15 as npi
from train_offense_gan_MAY29_class_updates_GPU1 import NPINetwork, GenerationClassifier

from train_offense_gan_MAY29_class_updates_GPU1 import GPT2WithNPI, GPT2LMWithNPI

import pdb
import pickle as pkl

#big_text_file = "smaller_wiki_books_reddit_shuffled.txt"
off_txt_pkl = "data/sexist_sents_1000.pkl"

offensive_words_document = "data/sexist_terms.txt"
with open(offensive_words_document,'r') as f:
    OFFENSIVE_WORDS = f.readlines()
OFFENSIVE_WORDS = [word.strip('\n') for word in OFFENSIVE_WORDS]

def my_accuracy(x, y):
    x, y = x.squeeze().data.cpu().numpy(), y.squeeze().data.cpu().numpy()
    x = np.array([round(xi) for xi in x])
    y = np.array([round(yi) for yi in y])
    if len(x) != 0:
        return len(x[x==y])/len(x)
    else:
        return 0.

def count_offensive(sent):
    sent = sent.lower()
    offense_count = 0
    for word in OFFENSIVE_WORDS:
        if word in sent:
            offense_count += sent.count(word)
    return offense_count

def filter_for_printing(sent):
    for word in OFFENSIVE_WORDS:
        sent = sent.replace(word, word[0] + "*"*(len(word)-2) + word[-1])
    return sent

def generate_text(in_text, lm_model, tokenizer, target_label=[1],
                      num_generation_iters=100, max_seq_len=15, num_samples=1,  
                      temperature=1, top_k=1, top_p=0.0):
    
    print("Generating text ordinarily",flush=True)

    tokens = tokenizer.encode(in_text)
    # process tokens
    tokens = tokens[-max_seq_len:]
    tokens = torch.tensor(tokens, dtype=torch.long) 
    tokens = tokens.unsqueeze(0).repeat(num_samples, 1) 
    tokens = tokens.cuda()
    lm_model = lm_model.cuda()
    lm_model.transformer.output_hidden_states = False

    num_tokens_needed = max_seq_len - tokens.shape[1]

    out_tokens = []
            
    # We loop through a few times now
    for i in range(num_tokens_needed):

        # Now run the model
        hidden_states, presents = lm_model(input_ids=tokens) 

        # Now we add the new token to the list of tokens
        next_token_logits = hidden_states[0,-1,:] # This is a very long vector (vocab size)
        filtered_logits = rg.top_k_top_p_filtering(next_token_logits, top_k=top_k, top_p=top_p)
        next_token = torch.multinomial(F.softmax(filtered_logits, dim=-1), num_samples=num_samples)
        next_token_list = next_token.tolist()
        out_tokens = out_tokens + next_token_list
        #next_word = tokenizer.decode(next_token_list)
        #out_text = out_text + " " + next_word
                
        # ...update list of tokens

        tokens = torch.cat((tokens,next_token.unsqueeze(0)),dim=1).cuda()

    for I in range(num_generation_iters):

        print(".",flush=True,end=" ")

        hidden_states, presents = lm_model(input_ids=tokens)

        # Now we add the new token to the list of tokens
        next_token_logits = hidden_states[0,-1,:] # This is a very long vector
        filtered_logits = rg.top_k_top_p_filtering(next_token_logits, top_k=top_k, top_p=top_p)
        next_token = torch.multinomial(F.softmax(filtered_logits, dim=-1), num_samples=num_samples)
        next_token_list = next_token.tolist()
        out_tokens = out_tokens + next_token_list
        #next_word = tokenizer.decode(next_token_list)
        #out_text = out_text + " " + next_word
                
        # ...update list of tokens

        tokens = torch.cat((tokens[:,1:],next_token.unsqueeze(0)),dim=1).cuda()
    
    print("", flush=True)

    return tokenizer.decode(out_tokens)



def generate_text_with_NPI(in_text, lm_model, vanilla_lm_model, tokenizer, perturbation_indices, npi_model, 
                      target_label=[1], num_generation_iters=100, num_seq_iters=15, max_seq_len=15, num_samples=1, 
                      temperature=1, top_k=1, top_p=0.0):

    print("Generating text with NPI perturbations",flush=True)

    lm_model.initialize_npi(perturbation_indices)
    
    tokens = tokenizer.encode(in_text)
    # process tokens
    tokens = tokens[-max_seq_len:]
    tokens = torch.tensor(tokens, dtype=torch.long) 
    tokens = tokens.unsqueeze(0).repeat(num_samples, 1) 
    tokens = tokens.cuda()
    lm_model = lm_model.cuda()

    vanilla_lm_model.transformer.output_hidden_states = False

    num_tokens_needed = max_seq_len - tokens.shape[1]

    out_tokens = []
            
    # We loop through a few times now
    for i in range(num_tokens_needed):

        # Now run the model
        hidden_states, presents = vanilla_lm_model(input_ids=tokens) 

        # Now we add the new token to the list of tokens
        next_token_logits = hidden_states[0,-1,:] / temperature # This is a very long vector
        filtered_logits = rg.top_k_top_p_filtering(next_token_logits, top_k=top_k, top_p=top_p)
        next_token = torch.multinomial(F.softmax(filtered_logits, dim=-1), num_samples=num_samples)
        next_token_list = next_token.tolist()
        out_tokens = out_tokens + next_token_list
        #next_word = tokenizer.decode(next_token_list)
        #out_text = out_text + " " + next_word
                
        # ...update list of tokens

        tokens = torch.cat((tokens,next_token.unsqueeze(0)),dim=1).cuda()

    vanilla_lm_model.transformer.output_hidden_states = True

    while len(out_tokens) < num_generation_iters:

        print(".",flush=True,end=' ')

        big_array = []

        # Loop through num_seq_iters iterations to collect activations in big_array
        for i in range(num_seq_iters):
            hidden_states, presents, all_hiddens = vanilla_lm_model(input_ids=tokens[:,-max_seq_len:])

            for pi in perturbation_indices:
                big_array.append(all_hiddens[pi])

            next_token_logits = hidden_states[0,-1,:] / temperature
            filtered_logits = rg.top_k_top_p_filtering(next_token_logits, top_k=top_k, top_p=top_p)
            next_token = torch.multinomial(F.softmax(filtered_logits, dim=-1), num_samples=num_samples)

            tokens = torch.cat((tokens,next_token.unsqueeze(0)),dim=1).cuda()
        
        tokens = tokens[:,:-num_seq_iters]

        big_array = torch.cat(big_array, dim=1).unsqueeze(3)
        npi_perturbations = npi_model(big_array)
        reshaped = npi_perturbations[:,:,:,0]
        chunked = torch.chunk(reshaped, max_seq_len*len(perturbation_indices), dim=1)
        curr_perturbs = [x.view(1, max_seq_len, -1) for x in chunked]

        for i in range(num_seq_iters):
            ith_perturbs = curr_perturbs[i*len(perturbation_indices):(i+1)*len(perturbation_indices)]
            # Now run the model
            hidden_states, presents, all_hiddens = \
                lm_model(input_ids=tokens[:,-max_seq_len:], activation_perturbations=ith_perturbs)                         

            # Now we extract the new token and add it to the list of tokens
            next_token_logits = hidden_states[0,-1,:] / temperature
            filtered_logits = rg.top_k_top_p_filtering(next_token_logits, top_k=top_k, top_p=top_p)
            next_token = torch.multinomial(F.softmax(filtered_logits, dim=-1), num_samples=num_samples)
            next_token_list = next_token.tolist()
            out_tokens = out_tokens + next_token_list # append to product here
            #next_word = tokenizer.decode(next_token_list)
            #sent = sent + " " + next_word # we just update this so sent remains accurate for dict
            #generated_sent = generated_sent + next_word + " "

            # ...update list of tokens
            tokens = torch.cat((tokens[:,1:],next_token.unsqueeze(0)),dim=1).cuda()

        tokens = tokens[:,-max_seq_len:]

        # Now repeat process again for another chunk of num_seq_iters until we have enough text generated

    print("",flush=True)

    return tokenizer.decode(out_tokens)


if __name__ == "__main__":

    target_word = "cat"

    # EDIT this section for desired model paths (to test)

    NPIs_to_test = [
            "npi_models/params_discco2.0_styco10.0_simco0.0_layers_2_9/adversarial_npi_network_epoch20.bin", 
            "npi_models/params_discco2.0_styco10.0_simco0.0_layers_2_9/adversarial_npi_network_epoch30.bin",
            "npi_models/params_discco2.0_styco10.0_simco0.0_layers_2_9/adversarial_npi_network_epoch40.bin",
            "npi_models/params_discco2.0_styco10.0_simco0.0_layers_2_9/adversarial_npi_network_epoch50.bin",
            "npi_models/params_discco2.0_styco10.0_simco0.0_layers_2_9/adversarial_npi_vfinal.bin",
            ]

    pis_list = [
            [5,11], 
            ] * len(NPIs_to_test) # KOMYA

    #path_to_npi = "/home/nate/MODELS/npi_proj/gan_GS/params_discco1_styco10_simco1_layers_5_11/adversarial_npi_network_epoch50.bin"
    #path_to_npi = "/home/nate/MODELS/npi_proj/gan_GS_FIXED/params_discco3.0_styco10.0_simco1.0_layers_0_6/adversarial_npi_network_epoch100.bin"
    
    for ind, (path_to_npi, perturbation_indices) in enumerate(zip(NPIs_to_test, pis_list)):

        print("")
        print("##########################################################")
        print("#### About to start testing for {} with perterub indices {}, test nubmer {} #####".format(path_to_npi, perturbation_indices, ind))
        print("#########################################################")
        print("")

        #user_input = ""#input("Press ENTER to proceed or type 'stop' to quit: ")
        #if 'stop' in user_input.lower() or 'quit' in user_input.lower():
        #    raise KeyboardInterrupt("System quit by user")

        outfile_name = 'offense_data/' + str(ind) + 'f'
        f = open(outfile_name + '_counts.txt', 'w')
        f.write(path_to_npi)
        f.write('\n')
        f.close()

        npi_model = torch.load(path_to_npi, map_location=torch.device('cpu'))

        vanilla_lm_model = GPT2LMHeadModel.from_pretrained("gpt2-medium")
        npi_lm_model = GPT2LMWithNPI.from_pretrained("gpt2-medium")
        tokenizer = GPT2Tokenizer.from_pretrained("gpt2-medium")
    
        # Make sure everything is on the same GPU
        npi_model = npi_model.cuda()
        vanilla_lm_model = vanilla_lm_model.cuda()
        npi_lm_model = npi_lm_model.cuda()

        in_texts_list = ["We're not going to be able to do that",
                        "How",
                        "Hello how are you",
                        "The first type",
                        "I like fish",
                        "Cats appeared in the alley",
                        "The supernova eclipsed"
                        ]

        in_texts_list = []

        with open(off_txt_pkl,'rb') as f:
            total_sents = pkl.load(f)#f.read().split(SYMBOL)

            iterator = 0
            for line in total_sents:
                if len(line) < 3 or len(line) > 1000:
                    continue
                in_texts_list.append(line)
                iterator += 1
                if iterator > 1000:
                    break
            del total_sents
 
        total_examples_evaluated = 0

        total_input_count = 0
        total_vanilla_count = 0
        total_perturbed_count = 0

        input_instances = 0
        vanilla_instances = 0
        perturbed_instances = 0

        switched_to_target = 0
        switched_from_target = 0

        num_vanilla_degenerate = 0
        num_perturbed_degenerate = 0
   

        for in_text in in_texts_list[:1000]:

            vanilla_text = generate_text(in_text, vanilla_lm_model, tokenizer)
            perturbed_text = generate_text_with_NPI(in_text, npi_lm_model, vanilla_lm_model, tokenizer, perturbation_indices, npi_model)

            print("******=========********")
            print("Input text",in_text)

            print("========")
            print("Vanilla_text:", vanilla_text)
            print("========")
            print("Perturbed text:", perturbed_text)
            print("========")


            input_instance = 0
            vanilla_instance = 0
            perturbed_instance = 0


            #total count
            total_examples_evaluated += 1
            print("")

            # count instances of target and target-plural in outpus
            total_input_count += in_text.lower().count(target_word.lower())
            total_vanilla_count += vanilla_text.lower().count(target_word.lower())
            total_perturbed_count += perturbed_text.lower().count(target_word.lower())

            # check for existence of target and target-plural in input and output
            if count_offensive(in_text.lower().replace(".", " ").replace("!"," ").replace("?"," ")):
                input_instances += 1
                input_instance = 1
            if count_offensive(vanilla_text.replace("."," ").replace("!"," ").replace("?"," ")):
                vanilla_instances += 1
                vanilla_instance = 1
            if count_offensive(perturbed_text.lower().replace("."," ").replace("!"," ").replace("?"," ")):
                perturbed_instances += 1
                perturbed_instance = 1

            # determine switched-to and switched-from
            if not vanilla_instance and perturbed_instance:
                switched_to_target += 1
            if vanilla_instance and not perturbed_instance:
                switched_from_target += 1


            # detect degenerate word repetitions
            t = vanilla_text.lower()
            if len(t.split()) - len(set(t.split())) > len(t.split())/4.0:
                num_vanilla_degenerate += 1
                print('VANILLA DEGENERATE')
            t = perturbed_text.lower()
            if len(t.split()) - len(set(t.split())) > len(t.split())/4.0:
                num_perturbed_degenerate += 1
                print('PERTURBED DEGENERATE')

            # save data
            data = []
            try:
                with open(outfile_name + '_texts.pkl', 'rb') as f:
                    data = pkl.load(f)
                    f.close()
            except:
                pass
            with open(outfile_name + '_texts.pkl', 'wb') as f:
                dict_ = {}
                dict_["input_text"] = in_text
                dict_["vanilla"] = vanilla_text
                dict_["perturbed"] = perturbed_text
                data.append(dict_)
                pkl.dump(data,f)
                f.close()
            with open(outfile_name + '_counts.txt', 'a') as f:
                f.write("total_examples_evaluated %d, total_vanilla_count %d, total_perturbed_count %d, input_instances %d, vanilla_instances %d, perturbed_instances %d, switched_to_target %d, switched_from_target %d, num_vanilla_degenerate %d, num_perturbed_degenerate %d \n"%(total_examples_evaluated, total_vanilla_count, total_perturbed_count, input_instances, vanilla_instances, perturbed_instances, switched_to_target, switched_from_target, num_vanilla_degenerate, num_perturbed_degenerate))
                f.write('\n')
            print("model name", outfile_name)
            print("total_examples_evaluated", total_examples_evaluated)
            print("total_perturbed_count", total_perturbed_count)
            print("total_vanilla_count", total_vanilla_count)



        print("============")
        print("total_examples_evaluated", total_examples_evaluated)
        print("total_input_count", total_input_count)
        print("total_vanilla_count", total_vanilla_count)
        print("total_perturbed_count", total_perturbed_count)
        print("")

        print("target {} present in GPT-2 input: {}".format(target_word, input_instances))
        print("target {} present in untouched GPT-2 output: {}".format(target_word, vanilla_instances))
        print("target {} present in perturbed GPT-2 output: {}".format(target_word, perturbed_instances))
        print("")
        print("switched_to_target", switched_to_target)
        print("switched_from_target",  switched_from_target)

