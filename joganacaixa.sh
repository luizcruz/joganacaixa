#!/bin/bash

echo -e "\n*******************************************************"
echo -e "** \033[32m Joga na caixa\033[39m - Versão 1.0                       **"
echo -e "*******************************************************\n"

if [ $# -eq 0 ]
  then
    echo -e "Sem argumentos. Use as opções:\n\n-i Inicializar\n-u Upload\n-l <expressão> Localizar arquivo e baixar\n-v Visualizar todos os arquivos armazenados\n"
fi

while [[ $# -gt 0 ]]
do
key="$1"

case $key in
    -i)
			#Garante que o o diretório do Google Cloud SDK está no PATH
			export PATH=~/Desktop/google-cloud-sdk/bin/:$PATH

			#Verifica se o comando gsutil está instalado. Se não estiver instala o SDK
			if [ -x gsutil ]; then
				echo -e "[\033[31m-\033[39m] Instalando SDK Google\n"

				echo "Para os processos a seguir você deve ter um projeto criado e um meio de pagamento associado"
				cd ~/ && wget https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-sdk-177.0.0-darwin-x86_64.tar.gz && tar -zxvf google-cloud-sdk-177.0.0-darwin-x86_64.tar.gz
				./google-cloud-sdk/install.sh
				./google-cloud-sdk/bin/gcloud init
				cd - 				

			else
				echo -e "[\033[32m+\033[39m] SDK Google instalado"
			fi

			#Garante que o o diretório executável do Python está no PATH para qualquer versão de Python instalada
			export PATH=~/Library/Python/`python --version 2>&1 | cut -f2 -d" " | cut -f1-2 -d"."`/bin/:$PATH

			#Verifica se o comando AWS está instalado. Se não estiver instala o SDK
			if [ -x aws ]; then
				echo -e "[\033[31m-\033[39m] Instalando SDK Amazon"
				pip install awscli
				aws configure
			else
				echo -e "[\033[32m+\033[39m] SDK Amazon instalado"
			fi

			exit 0 
    ;;
    -u)
			
			#Verifica se o diretório escorregador está criado
			if [ -d .escorregador/ ]; then
				echo -e "[\033[32m+\033[39m] Diretório escorregador existente"
			else
				mkdir .escorregador/ && echo -e "[\033[31m-\033[39m] Diretório escorregador criado"
			
			fi

			#Verifica se o diretório escorregador está criado
			if [ -d .etiqueta/ ]; then
				echo -e "[\033[32m+\033[39m] Diretório etiqueta existente"
			else
				mkdir .etiqueta/ && echo -e "[\033[31m-\033[39m] Diretório etiqueta criado"
			
			fi

			# Compacta todos os arquivos e move para escorregador
			TIMESTAMP=`date +%s`
			ARQUIVO='.escorregador/'$TIMESTAMP'.tar.gz'
			COPYFILE_DISABLE=true 
			tar -c --exclude-from=.tarignore -zcf $ARQUIVO . > /dev/null 2>&1 && echo -e "[\033[32m+\033[39m] Preparando arquivo para upload"
  			
			#Verifica se o bucket foi criado no Google
			bucket=`gsutil ls gs:// | grep gs://caixa/ | wc -l` 
			if [ $bucket -ne 0 ]; then
				echo -e "[\033[32m+\033[39m] Bucket existente na nuvem Google"
			else
				gsutil mb -c regional -l southamerica-east1 gs://caixa/ && echo -e "[\033[32m+\033[39m] Bucket caixa criado no Google"
			fi	

			#Verifica se o bucket foi criado no Amazon 
			bucket=`aws s3 ls | grep caixa | wc -l` 
			if [ $bucket -ne 0 ]; then
				echo -e "[\033[32m+\033[39m] Bucket existente na nuvem Amazon"
			else
				aws s3 mb s3://joganacaixa --region sa-east-1 && echo -e "[\033[32m+\033[39m] Bucket caixa criado na AWS"
				
			fi	



			#Escorrega os arquivos para o bucket
			cd .escorregador/ && gsutil cp *.tar.gz gs://caixa/ 2>&1 && aws s3 cp *.tar.gz s3://joganacaixa  && echo -e "[\033[32m+\033[39m] Arquivo copiado" && cd ..

			#Gera etiqueta
			cd .escorregador/ 
			tar --list --file=$TIMESTAMP.tar.gz > ../.etiqueta/$TIMESTAMP && echo -e "[\033[32m+\033[39m] Etiqueta gerada"  && cd ..
			
			#Apaga ARQUIVO enviado
			cd .escorregador/ && rm $TIMESTAMP.tar.gz && echo -e "[\033[32m+\033[39m] Arquivo enviado removido" && cd ..

			#Apaga todos os arquivos que foram copiados para o diretório
			for i in `cat .etiqueta/$TIMESTAMP`; do 
				if [ "$i" != "./" ]; then
					rm -i $i
				fi
			done

			exit 0 




    ;; 
    -l)
			#Verifica se tem a expressão
			if [ -z $2 ]; then
					echo -e "[\033[31m-\033[39m] Falta a expressão de busca\n"
				exit
			fi


			#Localiza ARQUIVO baseado em expressão e faz download
			ENCONTRADOS=`grep -R $2 .etiqueta/* | cut -d':' -f1 | cut -d'/' -f2 | wc -l`
			if [ $ENCONTRADOS -gt 0 ]; then
				echo -e "[\033[32m+\033[39m]Arquivos encontrados nestes pacotes (ordem cronológica):\n"
				grep -R $2 .etiqueta/* | cut -d':' -f1 | cut -d'/' -f2 
			fi


			#Escolhe qual baixar e baixa
			echo -e "\nEntre o arquivo que deseja baixar e pressione [Enter]: "
			read ARQUIVOD	
			
			#Baixa o pacote escolhido e descompacta
			if [  ${#ARQUIVOD} -gt 0 ]; then
				echo -e '[\033[32m+\033[39m] Baixando e descompactando arquivo escolhido'
				gsutil cp gs://caixa/$ARQUIVOD'.tar.gz'  .  
				tar zxvf *.tar.gz && rm *.tar.gz

			else
				echo -e "[\033[31m-\033[39m] Você não entrou com nenhum arquivo\n"
			fi
			exit 0
    ;;
    -v)
		#Lista de todos os arquivos armazenados
		echo -e "[\033[32m+\033[39m] Listando todos os arquivos válidos\n"
		more .etiqueta/*
		exit 0

	;;
    
    *)
			echo -e "[\033[31m-\033[39m] Você não entrou com nenhum parâmetro válido\n"
			exit 0
    ;;
esac
shift
done






