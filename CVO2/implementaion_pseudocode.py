import os
import time
import requests
import re
import mimetypes
import logging
import subprocess
from pathlib import Path
#Regrouper constante et paramètrage dont j'ai besoin
base_url = "http://localhost:3000"
headers = {
    "Authorization": "Bearer ol_api_t60E1Pdc4zUuhOfboJcA4pygYZxL1O6MQXBcgK",
    "Content-Type": "application/json"
}
chemin_collection = r"C:\Users\yndet6\export_outline\CLGbis"

LOG_FILE = Path(__file__).with_name("outline_import.log")

logging.basicConfig(
    filename=LOG_FILE,
    filemode="w",
    format="%(levelname)s %(asctime)s - %(name)s - %(message)s",
    level=logging.INFO,
    encoding="utf-8",
    force=True,
)

logger = logging.getLogger(__name__)

logger.info("Script démarré")
subprocess.Popen(["code", str(LOG_FILE)])

def post_avec_RetryAfter(url, headers, json=None, data=None, files=None, max_essais=5):
    """ Envoie une requête Post avec gestion des erreurs 429 """
    delai = 1
    for essai in range(1, max_essais + 1):
        demande = requests.post(url, headers=headers, json=json, data=data, files=files)

        try:
            contenu = demande.json()
        except ValueError:
            contenu = {}

        # Erreur HTTP
        if demande.status_code >= 400:
            logger.error("Erreur HTTP %s sur %s : %s",demande.status_code,url,demande.text)

        # Erreur applicative malgré un statut HTTP 200
        elif contenu.get("error") or contenu.get("errors"):
            logger.error("Erreur API malgré HTTP %s sur %s : %s",demande.status_code,url,contenu)

        if demande.status_code != 429:
            if demande.status_code >= 400:
                logger.error("Erreur HTTP %s sur %s : %s",demande.status_code,url,demande.text)
            return demande

        if essai == max_essais:
            logger.error("429 persistant après %s tentatives sur %s",max_essais,url)
            return demande

        retry_after = demande.headers.get("Retry-After")
        if retry_after is not None:
            try:
                delai = float(retry_after)
            except (TypeError, ValueError):
                delai *= 2

        logger.warning("429 reçu, attente %.1f secondes avant retry", delai)
        time.sleep(delai)
        delai = max(delai * 2, 1.0)
    return None

def creer_fichier_md_manquants(chemin_collection):
    """ Créer un fichier Md portant le nom du dossier qui n'as pas de fichiers équivalents"""
    for repertoire_courant, dossiers, fichier in os.walk(chemin_collection):
        for dossier in dossiers:
            if dossier == "attachments":
                continue

            chemin_md = os.path.join(repertoire_courant, dossier + ".md")
            if not os.path.isfile(chemin_md):
                titre_fichier = f"""# {dossier}"""
                with open(chemin_md, "w", encoding="utf-8") as fichier:
                    #Ecrire le titre du fichier markdown manquant correspondant au nom du dossier  
                    fichier.write(titre_fichier)
                logger.info("Création du fichier : %s", os.path.basename(chemin_md))

def creer_collection_outline(base_url, headers, nom_collection):
    """ Créer la collection Outline à partir du dossier racine """
    data_collection = {
        "name": nom_collection,
        "description": "",
        "permission": "read_write",
        "color": "#000000",
        "private": False
    }
    return post_avec_RetryAfter(f"{base_url}/api/collections.create", headers=headers, json=data_collection)

def trouver_document_parent(base_url, headers, collection_id, repertoire_utiliser):
    """ Recherche un document correspondant au nom du dossier courant """
    data_infos = {
        "collectionId": collection_id,
        "query": repertoire_utiliser
                    }
    infos_documents = post_avec_RetryAfter(f"{base_url}/api/documents.search", headers=headers, json=data_infos)

    if infos_documents is None:
        return None

    for valeur in infos_documents.json().get("data", []):
        document = valeur.get("document", {})
        if document.get("title") == repertoire_utiliser:
            return document

    return None

def uploader_attachments_pour_document(base_url, headers, dossier_attachment, document_id, texte):
    """Upload les attachments présent dans le contenu Markdown vers le document Outline"""
    if not os.path.isdir(dossier_attachment):
        return
        
    attachment_a_modifier = re.findall(r"\[([^\]]+)\]\((\./attachments(?:/[^\)]+)?)\)",texte)
    nom_attachment = {os.path.basename(chemin_attachment) for _, chemin_attachment in attachment_a_modifier}

    for attachment in os.listdir(dossier_attachment):
        chemin_attachment = os.path.join(dossier_attachment, attachment)
        if not os.path.isfile(chemin_attachment):
            continue

        if attachment not in nom_attachment:
            continue

        content_type, _ = mimetypes.guess_type(attachment)
        if content_type is None:
            content_type = "application/octet-stream"

        data_attachment = {
            "name": attachment,
            "contentType": content_type,
            "size": os.path.getsize(chemin_attachment),
            "documentId": document_id 
        }
        creation_attachment = post_avec_RetryAfter(f"{base_url}/api/attachments.create", headers=headers, json=data_attachment)

        if creation_attachment is None or creation_attachment.status_code >= 400:
            logger.error("Attachment non créé : %s | statut HTTP : %s | réponse : %s",attachment,creation_attachment.status_code
                if creation_attachment else "aucune réponse",
                creation_attachment.text
                if creation_attachment else "")
            continue

        logger.info("Attachment créé : %s | statut HTTP : %s",attachment,creation_attachment.status_code)

        attachment_data = creation_attachment.json().get("data", {})

        get_attachment_id = attachment_data.get("attachment", {})
        attachment_id = get_attachment_id.get("id")

        form_data = attachment_data.get("form", {})
        upload_key = form_data.get("key")

        if not upload_key:
            logger.error("Clé d'upload introuvable pour %s", attachment)
            continue

        with open(chemin_attachment, "rb") as fichier:
            files = {
                "file": (attachment, fichier, content_type)
            }
            data = {
                "key": upload_key,
                "Content-Type": content_type
            }
            headers_upload = {
                "Authorization": headers["Authorization"],
            }
            creation_fichier = post_avec_RetryAfter(f"{base_url}/api/files.create", headers=headers_upload, data=data, files=files)

            if creation_fichier is None or creation_fichier.status_code >= 400:
                logger.error("Attachment non envoyé : %s | statut HTTP : %s | réponse : %s",attachment,creation_fichier.status_code
                    if creation_fichier else "aucune réponse",
                    creation_fichier.text
                    if creation_fichier else "")
                continue

            logger.info("Attachment envoyé : %s | statut HTTP : %s",attachment,creation_fichier.status_code)

            id_doc_a_modif = {
                "id": document_id
            }
            doc_a_modif = post_avec_RetryAfter(f"{base_url}/api/documents.info", headers=headers_upload, data=id_doc_a_modif, files=files)
           

            texte_document = doc_a_modif.json().get("data", {}).get("text")

            texte_modifie = texte_document

            pattern_images = rf"!\[([^\]]*)\]\(\.?/attachments/{re.escape(attachment)}\)"
            pattern_fichiers = rf"\[([^\]]*)\]\(\.?/attachments/{re.escape(attachment)}\)"
            
            if content_type.startswith("image/"):
                texte_modifie = re.sub(pattern_images, rf"![]({base_url}/api/attachments.redirect?id={attachment_id})", texte_document)
            else:
                texte_modifie = re.sub(pattern_fichiers, rf"[\1]({base_url}/api/attachments.redirect?id={attachment_id})", texte_document)  
            data_update = {
                "id": document_id,
                "text": texte_modifie
            }
            update_attachment = post_avec_RetryAfter(f"{base_url}/api/documents.update", headers=headers_upload, data=data_update, files=files)
            if creation_attachment is None or creation_attachment.status_code >= 400:
                logger.error("Attachment non uploadé : %s | statut HTTP : %s | réponse : %s", attachment,update_attachment.status_code)
                

def nettoyage_document(texte):
    """ A vocation de supprimer les balises html <a id= </a> ainsi que le texte entre parenthèses situés en dessous des emojis """
    texte = re.sub(r"<a\b[^>]*>.*?</a>", "", texte, flags=re.IGNORECASE | re.DOTALL)
    # Supprime la séquence (étoile bleue) présente sous les emojis
    texte = re.sub(r"\s*\(étoile bleue\)\s*", "", texte)
    texte = re.sub(r"(?m)^\s*[*+-]\s+\[[^\]]*\]\(#[^)]+\)\s*$\n?","",texte)
    texte = re.sub(r"&&PATH_ORIGINEL=(.*?)&&", "", texte)
    return texte

def conversion_liens(base_url, headers, collection_id, chemin_collection):
    pattern_tatouage = r"&&PATH_ORIGINEL=(.*?)&&"
    pattern_liens = r"\((?:https://)?(?:\./|\.\./)([^)]+)\)"
    annuaire_tatouage_url = {}

    tous_les_documents = []
    offset = 0
    limit = 100

    while True:
        data_liste = {
            "offset": offset,
            "collectionId": collection_id,
            "limit": limit
        }

        reponse = post_avec_RetryAfter(
            f"{base_url}/api/documents.list",
            headers=headers,
            json=data_liste
        )

        documents = reponse.json().get("data", [])
        tous_les_documents.extend(documents)

        if len(documents) < limit:
            break

        offset += limit

    extraction_data_liste = tous_les_documents

    for document in extraction_data_liste:
        url = document.get("url")
        texte = document.get("text")
        correspondance_tatouage = re.search(pattern_tatouage, texte)
        if correspondance_tatouage:
            chemin_originel = correspondance_tatouage.group(1)
            chemin_originel = os.path.normpath(chemin_originel)
            chemin_relatif = os.path.relpath(
                chemin_originel,
                os.path.normpath(chemin_collection)
            ).replace("\\", "/")
            annuaire_tatouage_url[chemin_relatif] = url

    for document in extraction_data_liste:
        documentId = document.get("id")
        texte = document.get("text")
        texte_modifie = texte
        for match in re.finditer(pattern_liens, texte):
            liens = match.group(1)
            if liens in annuaire_tatouage_url:
                url_outline = base_url + annuaire_tatouage_url[liens]
                texte_modifie = texte_modifie.replace(match.group(0), f"({url_outline})")
        texte_modifie = nettoyage_document(texte_modifie)
        data_final = {
            "id": documentId,
            "text": texte_modifie
        }
        post_avec_RetryAfter(f"{base_url}/api/documents.update", headers=headers, json=data_final)

# Création des fichiers Markdown manquants
creer_fichier_md_manquants(chemin_collection)

# Création de la collection (dossier racine) Outline
creation_collection = creer_collection_outline(base_url, headers, os.path.basename(chemin_collection))

if creation_collection is None or creation_collection.status_code >= 400:
        logger.error("Impossible de créer la collection Outline")
        raise RuntimeError("Création de collection échouée")

collection_id = creation_collection.json().get("data", {}).get("id")

for repertoire_courant, _, fichiers in os.walk(chemin_collection):
    dossier_attachment = os.path.join(repertoire_courant, "attachments")

    for fichier in fichiers:
        if os.path.splitext(fichier)[1].lower() != ".md":
            continue

        chemin_fichier = os.path.join(repertoire_courant, fichier)
        with open(chemin_fichier, "r", encoding="utf-8") as f:
            texte = f.read()
            texte = f"&&PATH_ORIGINEL={os.path.normpath(chemin_fichier)}&&\n\n{texte}"
        nom_fichier = os.path.splitext(fichier)[0]
        document_parent_id = ""

        if repertoire_courant != chemin_collection:
            repertoire_utiliser = os.path.basename(repertoire_courant)
            document_parent = trouver_document_parent(base_url, headers, collection_id, repertoire_utiliser)
            if document_parent is not None:
                document_parent_id = document_parent["id"]

        if document_parent_id:
            data_fichier = {
                "title": nom_fichier,
                "parentDocumentId": document_parent_id,
                "text": texte,
                "publish": True
            }
        else:
            data_fichier = {
                "title": nom_fichier,
                "collectionId": collection_id,
                "text": texte,
                "publish": True
            }
        creation_document = post_avec_RetryAfter(f"{base_url}/api/documents.create", headers=headers, json=data_fichier)
        document_id = creation_document.json().get("data",{}).get("id")
        uploader_attachments_pour_document(base_url, headers, dossier_attachment, document_id, texte)
conversion_liens(base_url, headers, collection_id, chemin_collection)




